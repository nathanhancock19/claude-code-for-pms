#!/usr/bin/env python3
"""
swms.py - Dunsteel SWMS generator (wrapper around the existing SWMS pattern).

This is the worker behind the /swms skill. It is a thin wrapper that:
  1. Looks up the project folder on the S: drive by number prefix.
  2. Reads light project context (head contractor + project name from the folder
     name; for 501 the known Riverside context is built in).
  3. Calls Claude (claude-sonnet-4-6) with a structured prompt to produce a SWMS
     payload that matches the schema used by the existing Dunsteel generators
     (scripts/generate_riverside_swms*.py, scripts/generate_rigging_swms_payload.py).
  4. Renders that payload to HTML using a template that mirrors the live Dunsteel
     SWMS document quality (hazard/control tables, HRA checklist, PPE, plant,
     permits, qualifications, consultation, revision history, sign-off).
  5. Converts the HTML to PDF via scripts/html_to_pdf.py (Chrome headless - the
     mandated HTML->PDF path).
  6. Saves to the project's "15 WHS and Environmental" folder by default, or to a
     given output directory.

The payload SCHEMA is reused verbatim from the existing generators - that is the
shared contract. Claude fills the schema from the free-text activity description
instead of it being hand-written each time.

Usage:
    python scripts/swms.py 501 "Stair 6 reinstatement - hot works + EWP + WAH"
    python scripts/swms.py 501 "Bolt tightening on Level 7" --out-dir "C:/tmp"
    python scripts/swms.py 501 "Test activity" --test

Flags:
    --out-dir DIR   Override the save directory (instead of "15 WHS and Environmental").
    --test          Force output into a workspace temp folder; never write to the
                    live S: drive. Used for verification runs.
    --no-pdf        Skip the PDF conversion (HTML only).

Environment:
    ANTHROPIC_API_KEY   If set, the script calls claude-sonnet-4-6 to build the
                        payload. If NOT set, the script falls back to a built-in
                        deterministic sample payload so the render/PDF pipeline can
                        still run (it reports clearly that the fallback was used).

HARD RULE: no long dashes anywhere in this file or in generated content. Use a
hyphen, a colon, or restructure.
"""

import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPTS = WORKSPACE / "scripts"
S_CURRENT_PROJECTS = Path(r"S:\Operations\01 Current Project")
WHS_FOLDER_NAME = "15 WHS and Environmental"
MODEL = "claude-sonnet-4-6"

# Shared project resolver (brief front matter + project outputs folder). Used as
# the fallback for context and save location when the S: drive is absent (VPS).
sys.path.insert(0, str(SCRIPTS))
try:
    import dunsteel_projects as _projects  # noqa: E402
except Exception:  # noqa: BLE001
    _projects = None


def load_dotenv():
    """Populate os.environ from the workspace-root .env (zero dependency).

    Does not overwrite a value already present in the real environment, so an
    explicitly exported variable still wins over the file.
    """
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass

# Known project context (no project brief file exists for 501 yet). Folder name
# parsing covers head contractor + project name for every other project.
KNOWN_PROJECTS = {
    "501": {
        "project_name": "Stratus Data Centres SYD2 - Riverside",
        "builder": "Northbridge Constructions",
        "builder_contact": "Site Manager",
        "builder_phone": "04XX XXX XXX",
        "builder_email": "sitemanager@headcontractor.example.com",
        "location": "[site address]",
    },
}

# Dunsteel default contact block (matches the existing generators).
DUNSTEEL = {
    "company": "Dunsteel Pty Ltd",
    "contact_name": "Nathan Hancock",
    "contact_phone": "04XX XXX XXX",
    "contact_email": "nathanh@dunsteel.com.au",
}


# ---------------------------------------------------------------------------
# Project lookup
# ---------------------------------------------------------------------------

def find_project_folder(number: str):
    """Return the S: drive folder Path matching '[number] -*', or None."""
    if not S_CURRENT_PROJECTS.exists():
        return None
    matches = [p for p in S_CURRENT_PROJECTS.iterdir()
               if p.is_dir() and re.match(rf"^{re.escape(number)}\s*-", p.name)]
    return matches[0] if matches else None


def parse_folder_name(folder_name: str):
    """Split '[number] - [head contractor] - [project name]' into parts."""
    parts = [p.strip() for p in folder_name.split(" - ")]
    out = {"number": parts[0] if parts else "", "builder": "", "project_name": ""}
    if len(parts) >= 3:
        out["builder"] = parts[1]
        out["project_name"] = " - ".join(parts[2:])
    elif len(parts) == 2:
        out["project_name"] = parts[1]
    return out


def build_project_context(number: str, folder: Path):
    """Merge known context + folder-name parsing into a context dict."""
    ctx = {
        "project_number": number,
        "project_name": "",
        "builder": "",
        "builder_contact": "",
        "builder_phone": "",
        "builder_email": "",
        "location": "",
    }
    if folder is not None:
        ctx.update({k: v for k, v in parse_folder_name(folder.name).items()
                    if k in ctx and v})
    # VPS / no-S: fallback: fill any gaps from the project brief front matter.
    if _projects is not None and not (ctx["project_name"] and ctx["builder"]):
        try:
            r = _projects.resolve(number)
            if not ctx["project_name"] and r.get("name"):
                ctx["project_name"] = r["name"]
            if not ctx["builder"] and r.get("head_contractor"):
                ctx["builder"] = r["head_contractor"]
            if not ctx["location"] and r.get("location"):
                ctx["location"] = r["location"]
        except Exception:  # noqa: BLE001
            pass
    if number in KNOWN_PROJECTS:
        ctx.update(KNOWN_PROJECTS[number])
    return ctx


# ---------------------------------------------------------------------------
# Claude payload generation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a WHS document specialist preparing a Safe Work Method Statement (SWMS) for Dunsteel Pty Ltd, an Australian structural steel fabrication and installation company. You produce SWMS content that matches Australian WHS Regulation 2017 (NSW) requirements and Dunsteel's existing SWMS structure.

You output ONLY a single JSON object that conforms exactly to the schema described below. No prose, no markdown fences, no commentary.

The JSON schema (every key required):
{
  "swms_name": str,                 // short title, e.g. "Project Name - Activity"
  "revision": "1",
  "revision_date": str,             // ISO date YYYY-MM-DD
  "review_due": "6 monthly",
  "task_description": str,          // 2-4 sentences describing the scope of works
  "supporting_docs": [str],         // 5-9 referenced documents
  "hazardous_materials": str,       // newline-separated bullet lines, or a single sentence if none
  "consultation": str,              // how the SWMS was developed in consultation with workers
  "hrcw": [ {"checked": bool, "label": str} ],  // the 18 standard high-risk construction work categories below, with the relevant ones ticked
  "qualifications": [str],          // tickets/licences/training required for this scope
  "plant": [str],                   // plant and equipment used
  "ppe": [str],                     // PPE required
  "permits": [str],                 // permits required (hot works, working at heights, etc.)
  "activities": [
    {
      "name": str,                  // numbered activity stage, e.g. "1. Site Establishment"
      "rows": [
        {
          "hrcw": str,              // which HRCW category this step relates to, or "-" if none
          "step": str,              // the work step
          "hazards": str,           // the hazard arising from the step
          "initial": str,           // initial risk rating, e.g. "VH20", "H16", "M9", "L4"
          "controls": str,          // the control measures (hierarchy of control)
          "residual": str           // residual risk rating after controls
        }
      ]
    }
  ],
  "revisions": [ {"revision": "1", "date": str, "details": str, "changed_by": "Nathan Hancock", "approved_by": ""} ],
  "footer_note": str
}

The 18 standard HRCW categories (use these exact labels, set checked true/false based on the activity):
1. Risk of falls from greater than 2 metres
2. Work on a telecommunications tower
3. Demolition of load-bearing structure
4. Likely to involve disturbing asbestos
5. Temporary load-bearing support structures
6. Work in confined spaces
7. Work in or near shaft or trench with an excavated depth greater than 1.5m or a Tunnel
8. Use of explosives
9. Work on or near pressurised gas pipes or mains
10. Work on or near chemical, fuel, or refrigerant lines
11. Work on or near energised electrical installations or services
12. Work in an area with contaminated or flammable atmosphere
13. Work with tilt up or pre-cast concrete
14. Work on, in or adjacent to a road, railway, shipping lane or other traffic corridor in use by traffic other than pedestrians
15. Work in an area with movement of powered mobile plant
16. Work in or near water or other liquid that involves a risk of drowning
17. Diving work
18. Work in or around areas with artificial extremes of temperature

Risk rating convention (matches Dunsteel existing SWMS): a letter band (L/M/H/VH) followed by a risk score number, e.g. "VH20", "H16", "M9", "L4". Residual ratings must be lower than initial ratings after controls are applied.

Controls must follow the hierarchy of control and be specific and practical for structural steel work. Reference real plant, tickets, and Australian Standards where relevant (AS 1891.4 for harnesses, AS 4100, AS/NZS 1576 scaffold, etc.). Each activity should have 2-4 rows. Produce 5-9 activities covering the full work sequence (site establishment, delivery/crane, access/EWP/scaffold, the core task, finishing, emergency/rescue as relevant).

Do not use long dashes anywhere. Use a hyphen, a colon, or restructure the sentence."""


def build_user_prompt(ctx: dict, activity_description: str, today: str) -> str:
    return f"""Produce the SWMS JSON for the following:

Company performing the work: Dunsteel Pty Ltd
Project number: {ctx['project_number']}
Project name: {ctx['project_name']}
Head contractor (builder): {ctx['builder']}
Location: {ctx['location']}
Today's date: {today}

Activity / scope of works (free text from the project coordinator):
{activity_description}

Generate the complete SWMS as a single JSON object per the schema. Tailor the high-risk construction work checklist, hazards, controls, PPE, plant, permits, qualifications, and consultation to this specific activity and to structural steel work practices."""


def generate_payload_via_claude(ctx: dict, activity_description: str, today: str):
    """Build the SWMS payload on the Claude Max subscription. Returns dict or None."""
    load_dotenv()
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import claude_max
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        return claude_max.complete_json(
            build_user_prompt(ctx, activity_description, today),
            system=SYSTEM_PROMPT, model="sonnet")
    except Exception as e:  # noqa: BLE001 - fall back to the deterministic sample
        print(f"  Claude (Max) call failed ({e}); using fallback payload.")
        return None


def fallback_payload(ctx: dict, activity_description: str, today: str) -> dict:
    """Deterministic sample payload used when no API key is available.

    Mirrors the structure and quality of the existing live 501 SWMS so the
    render/PDF pipeline can be exercised and verified offline.
    """
    return {
        "swms_name": f"{ctx['project_name']} - {activity_description}".strip(" -"),
        "revision": "1",
        "revision_date": today,
        "review_due": "6 monthly",
        "task_description": (
            f"Structural steel works at {ctx['location'] or ctx['project_name']} "
            f"for {ctx['builder'] or 'the principal contractor'}. Scope: {activity_description}. "
            "Works include site establishment, access setup, the nominated steel activity, "
            "and progressive housekeeping and edge protection management."
        ),
        "supporting_docs": [
            "Project Drawings and Specifications",
            "Manufacturer Equipment Manuals",
            "Site-Specific Induction",
            "Emergency Response Procedures",
            "Dunsteel Lifting Gear Register",
            "Relevant Safety Data Sheets (SDS)",
        ],
        "hazardous_materials": (
            "For information on Hazardous Materials including use, clean up and first aid refer to SDS provided.\n"
            "- Diesel Fuel\n"
            "- Crystalline silica dust (if drilling into concrete) - refer to Dunsteel Silica Risk Assessment"
        ),
        "consultation": (
            "This SWMS has been developed in consultation with workers and/or their health and safety "
            "representative. Persons consulted: Site Supervisor, Project Manager, Principal Contractor Representative."
        ),
        "hrcw": [
            {"checked": True,  "label": "Risk of falls from greater than 2 metres"},
            {"checked": False, "label": "Work on a telecommunications tower"},
            {"checked": False, "label": "Demolition of load-bearing structure"},
            {"checked": False, "label": "Likely to involve disturbing asbestos"},
            {"checked": False, "label": "Temporary load-bearing support structures"},
            {"checked": False, "label": "Work in confined spaces"},
            {"checked": False, "label": "Work in or near shaft or trench with an excavated depth greater than 1.5m or a Tunnel"},
            {"checked": False, "label": "Use of explosives"},
            {"checked": False, "label": "Work on or near pressurised gas pipes or mains"},
            {"checked": False, "label": "Work on or near chemical, fuel, or refrigerant lines"},
            {"checked": False, "label": "Work on or near energised electrical installations or services"},
            {"checked": False, "label": "Work in an area with contaminated or flammable atmosphere"},
            {"checked": False, "label": "Work with tilt up or pre-cast concrete"},
            {"checked": False, "label": "Work on, in or adjacent to a road, railway, shipping lane or other traffic corridor in use by traffic other than pedestrians"},
            {"checked": True,  "label": "Work in an area with movement of powered mobile plant"},
            {"checked": False, "label": "Work in or near water or other liquid that involves a risk of drowning"},
            {"checked": False, "label": "Diving work"},
            {"checked": False, "label": "Work in or around areas with artificial extremes of temperature"},
        ],
        "qualifications": [
            "General Construction Induction Card (White Card) - all workers",
            "Site-Specific Induction - principal contractor",
            "Working at Heights Certificate - all workers at height",
            "EWP / Scissor Lift Ticket (Yellow Card) - EWP operators",
            "High Risk Work Licence - as applicable to plant in use",
        ],
        "plant": [
            "Scissor Lift / EWP (access and rescue standby)",
            "Hand and power tools (rated and tagged)",
            "Lifting slings, shackles, tag lines",
            "Safety harness + SRL or lanyard (AS 1891.4)",
        ],
        "ppe": [
            "Hard hat",
            "High-visibility vest",
            "Steel-capped safety boots",
            "Safety glasses",
            "Gloves",
            "Hearing protection (as required)",
            "Full-body harness and SRL or shock-absorbing lanyard (AS 1891.4)",
        ],
        "permits": [
            "Working at Heights Permit (obtained from Site Supervisor / Project Manager)",
            "Hot Works Permit (if any hot works are involved)",
        ],
        "activities": [
            {
                "name": "1. Site Establishment and Access",
                "rows": [
                    {
                        "hrcw": "Movement of powered mobile plant",
                        "step": "Mobilise to work area and set up exclusion zones",
                        "hazards": "Collision between workers, plant, and other trades",
                        "initial": "H16",
                        "controls": ("Exclusion zone established and marked around the work area. Spotter used where "
                                     "plant is operating. All workers in hi-vis at all times. Pre-start briefing held. "
                                     "Non-essential personnel excluded from the work zone."),
                        "residual": "L4",
                    },
                    {
                        "hrcw": "-",
                        "step": "Manual handling of materials and tools",
                        "hazards": "Musculoskeletal injury from lifting heavy or awkward items",
                        "initial": "M9",
                        "controls": ("Team lift for heavy or awkward items. Mechanical assist used where available. "
                                     "Manual handling technique: bend knees, back straight, load close to body."),
                        "residual": "L3",
                    },
                ],
            },
            {
                "name": "2. Working at Heights via EWP",
                "rows": [
                    {
                        "hrcw": "Risk of falls from greater than 2 metres",
                        "step": "Pre-operation of EWP / scissor lift",
                        "hazards": "Plant failure or unserviceable condition",
                        "initial": "VH20",
                        "controls": ("Complete daily pre-operational checks and fill in logbook at start of shift. "
                                     "If a fault is found, tag out of service, record in logbook, remove key, report "
                                     "to supervisor. Do not operate unserviceable plant."),
                        "residual": "M10",
                    },
                    {
                        "hrcw": "Risk of falls from greater than 2 metres",
                        "step": "Operating the EWP at height",
                        "hazards": "Fall from platform, tip over, or overloading",
                        "initial": "VH20",
                        "controls": ("Only Yellow Card endorsed operators to use the EWP. Set up on flat, level, solid "
                                     "ground. Full-body harness anchored to manufacturer nominated anchor point. Do not "
                                     "exceed SWL. Exclusion zone maintained below the platform. Never override safety systems."),
                        "residual": "M10",
                    },
                ],
            },
            {
                "name": "3. Nominated Steel Activity",
                "rows": [
                    {
                        "hrcw": "Risk of falls from greater than 2 metres",
                        "step": f"Carry out the nominated works: {activity_description}",
                        "hazards": "Fall from height; dropped tools or materials onto persons below",
                        "initial": "VH20",
                        "controls": ("Safety harness and SRL or lanyard attached to a rated anchor wherever guardrail "
                                     "is not in place. All hand tools tethered when working at height. Exclusion zone "
                                     "maintained below the work area. Hard hats mandatory for all personnel below. "
                                     "Rescue plan briefed to all workers prior to commencement."),
                        "residual": "M10",
                    },
                    {
                        "hrcw": "-",
                        "step": "Use of hand and power tools",
                        "hazards": "Laceration, eye injury, or contact with rotating parts",
                        "initial": "M9",
                        "controls": ("Tools inspected and tagged prior to use. Guards in place at all times. Safety "
                                     "glasses worn during cutting, drilling, or grinding. Only competent workers to "
                                     "operate power tools. Isolate tools when not in use."),
                        "residual": "L3",
                    },
                ],
            },
            {
                "name": "4. Emergency Response and Rescue",
                "rows": [
                    {
                        "hrcw": "Risk of falls from greater than 2 metres",
                        "step": "Worker arrested and suspended in harness at height",
                        "hazards": "Suspension trauma",
                        "initial": "H15",
                        "controls": ("Rescue plan briefed and rehearsed with all crew before works commence. EWP / "
                                     "scissor lift on standby for retrieval. Maximum 5-minute recovery target. Qualified "
                                     "First Aider on site at all times. If worker is unconscious or non-responsive, call "
                                     "000 immediately without waiting."),
                        "residual": "M10",
                    },
                ],
            },
        ],
        "revisions": [
            {"revision": "1", "date": today,
             "details": f"Initial issue - {activity_description}.",
             "changed_by": "Nathan Hancock", "approved_by": ""},
        ],
        "footer_note": (f"Document generated by Dunsteel Pty Ltd | Project {ctx['project_number']} - "
                        f"{ctx['project_name']} | Rev 1, {today}"),
    }


# ---------------------------------------------------------------------------
# HTML rendering (mirrors the live Dunsteel SWMS document structure)
# ---------------------------------------------------------------------------

def esc(value) -> str:
    return html.escape(str(value or ""))


def render_html(payload: dict, ctx: dict) -> str:
    p = payload

    def field_rows(items, cols=2):
        cells = "".join(f"<li>{esc(i)}</li>" for i in items)
        return f"<ul class='cols'>{cells}</ul>"

    # HRCW two-column checklist
    hrcw_cells = ""
    for item in p.get("hrcw", []):
        box = "checked" if item.get("checked") else ""
        cls = "hrcw-on" if item.get("checked") else "hrcw-off"
        mark = "X" if item.get("checked") else "&nbsp;"
        hrcw_cells += (f"<div class='hrcw-item {cls}'>"
                       f"<span class='box'>{mark}</span> {esc(item.get('label'))}</div>")

    # Hazardous materials - preserve newlines as line breaks
    haz = esc(p.get("hazardous_materials", "")).replace("\n", "<br>")

    # Activity tables
    activity_blocks = ""
    for act in p.get("activities", []):
        rows = ""
        for r in act.get("rows", []):
            rows += (
                "<tr>"
                f"<td>{esc(r.get('hrcw'))}</td>"
                f"<td>{esc(r.get('step'))}</td>"
                f"<td>{esc(r.get('hazards'))}</td>"
                f"<td class='rating'>{esc(r.get('initial'))}</td>"
                f"<td>{esc(r.get('controls'))}</td>"
                f"<td class='rating'>{esc(r.get('residual'))}</td>"
                "</tr>"
            )
        activity_blocks += f"""
        <table class="haztable">
          <tr class="acttitle"><td colspan="6">{esc(act.get('name'))}</td></tr>
          <tr class="acthead">
            <th>HRCW Category</th><th>Work Step</th><th>Hazard</th>
            <th>Initial Risk</th><th>Control Measures</th><th>Residual Risk</th>
          </tr>
          {rows}
        </table>
        """

    # Revision history
    rev_rows = ""
    for rv in p.get("revisions", []):
        rev_rows += (
            "<tr>"
            f"<td>{esc(rv.get('revision'))}</td>"
            f"<td>{esc(rv.get('date'))}</td>"
            f"<td>{esc(rv.get('details'))}</td>"
            f"<td>{esc(rv.get('changed_by'))}</td>"
            f"<td>{esc(rv.get('approved_by'))}</td>"
            "</tr>"
        )

    # Sign-off table (11 blank rows, matching the live template)
    signoff_rows = ""
    for _ in range(11):
        signoff_rows += "<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>"

    supporting = "".join(f"<li>{esc(d)}</li>" for d in p.get("supporting_docs", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(p.get('swms_name'))}</title>
<style>
  @page {{ size: A4 landscape; margin: 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt; color: #1a1a1a; margin: 0; }}
  h1 {{ font-size: 16pt; margin: 0 0 4px 0; color: #c8102e; }}
  h2 {{ font-size: 11pt; margin: 16px 0 6px 0; padding: 4px 8px; background: #1a1a1a; color: #fff; }}
  .header {{ border-bottom: 3px solid #c8102e; padding-bottom: 8px; margin-bottom: 10px; }}
  .meta {{ width: 100%; border-collapse: collapse; margin-bottom: 6px; }}
  .meta td {{ border: 1px solid #999; padding: 3px 6px; vertical-align: top; }}
  .meta .lbl {{ background: #f0f0f0; font-weight: bold; width: 16%; }}
  ul.cols {{ columns: 2; -webkit-columns: 2; margin: 4px 0; padding-left: 18px; }}
  ul {{ margin: 4px 0; padding-left: 18px; }}
  .hrcw-grid {{ columns: 2; -webkit-columns: 2; }}
  .hrcw-item {{ padding: 2px 0; break-inside: avoid; font-size: 9pt; }}
  .hrcw-item .box {{ display: inline-block; width: 14px; height: 14px; border: 1px solid #333;
                     text-align: center; line-height: 14px; font-weight: bold; margin-right: 4px; }}
  .hrcw-on {{ font-weight: bold; }}
  .hrcw-on .box {{ background: #c8102e; color: #fff; border-color: #c8102e; }}
  table.haztable {{ width: 100%; border-collapse: collapse; margin: 8px 0 14px 0; break-inside: auto; }}
  table.haztable td, table.haztable th {{ border: 1px solid #888; padding: 4px 5px; vertical-align: top;
                                          font-size: 8.5pt; }}
  tr.acttitle td {{ background: #e8761a; color: #fff; font-weight: bold; font-size: 10pt; padding: 5px 8px; }}
  tr.acthead th {{ background: #1a1a1a; color: #fff; font-size: 8.5pt; }}
  td.rating {{ text-align: center; font-weight: bold; white-space: nowrap; }}
  table.simple {{ width: 100%; border-collapse: collapse; }}
  table.simple td, table.simple th {{ border: 1px solid #888; padding: 4px 6px; font-size: 9pt; text-align: left; }}
  table.simple th {{ background: #1a1a1a; color: #fff; }}
  .footer {{ margin-top: 14px; padding-top: 6px; border-top: 1px solid #ccc; font-size: 8pt; color: #555; }}
  .note {{ font-size: 8pt; color: #555; margin: 2px 0 8px 0; }}
</style>
</head>
<body>

<div class="header">
  <h1>SAFE WORK METHOD STATEMENT</h1>
  <div style="font-size:11pt;font-weight:bold;">{esc(p.get('swms_name'))}</div>
</div>

<table class="meta">
  <tr>
    <td class="lbl">Company</td><td>{esc(DUNSTEEL['company'])}</td>
    <td class="lbl">Project No</td><td>{esc(ctx['project_number'])}</td>
    <td class="lbl">Revision</td><td>{esc(p.get('revision'))}</td>
  </tr>
  <tr>
    <td class="lbl">Dunsteel Contact</td><td>{esc(DUNSTEEL['contact_name'])} - {esc(DUNSTEEL['contact_phone'])}</td>
    <td class="lbl">Head Contractor</td><td>{esc(ctx['builder'])}</td>
    <td class="lbl">Revision Date</td><td>{esc(p.get('revision_date'))}</td>
  </tr>
  <tr>
    <td class="lbl">Email</td><td>{esc(DUNSTEEL['contact_email'])}</td>
    <td class="lbl">Builder Contact</td><td>{esc(ctx['builder_contact'])} {('- ' + ctx['builder_phone']) if ctx['builder_phone'] else ''}</td>
    <td class="lbl">Review Due</td><td>{esc(p.get('review_due'))}</td>
  </tr>
  <tr>
    <td class="lbl">Location</td><td colspan="5">{esc(ctx['location'])}</td>
  </tr>
</table>

<h2>Description of Works</h2>
<p>{esc(p.get('task_description'))}</p>

<h2>High Risk Construction Work (HRCW) Categories</h2>
<div class="note">Ticked categories apply to this scope of works.</div>
<div class="hrcw-grid">{hrcw_cells}</div>

<h2>Supporting Documents</h2>
<ul class="cols">{supporting}</ul>

<h2>Required Qualifications, Licences and Training</h2>
{field_rows(p.get('qualifications', []))}

<h2>Plant and Equipment</h2>
{field_rows(p.get('plant', []))}

<h2>Personal Protective Equipment (PPE)</h2>
{field_rows(p.get('ppe', []))}

<h2>Permits Required</h2>
{field_rows(p.get('permits', []))}

<h2>Hazardous Materials</h2>
<p>{haz}</p>

<h2>Hazard Identification and Control Measures</h2>
{activity_blocks}

<h2>Consultation</h2>
<p>{esc(p.get('consultation'))}</p>

<h2>Revision History</h2>
<table class="simple">
  <tr><th>Rev</th><th>Date</th><th>Details</th><th>Changed By</th><th>Approved By</th></tr>
  {rev_rows}
</table>

<h2>Worker Acknowledgment and Sign-Off</h2>
<div class="note">All workers must read, understand, and sign this SWMS prior to commencing work.</div>
<table class="simple">
  <tr><th>Name</th><th>Company</th><th>Signature</th><th>Date</th></tr>
  {signoff_rows}
</table>

<div class="footer">{esc(p.get('footer_note'))}</div>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# PDF conversion via the mandated Chrome headless path
# ---------------------------------------------------------------------------

def convert_to_pdf(html_path: Path) -> Path:
    converter = SCRIPTS / "html_to_pdf.py"
    subprocess.run([sys.executable, str(converter), str(html_path)], check=True)
    pdf_path = html_path.with_suffix(".pdf")
    if not pdf_path.exists():
        raise RuntimeError("PDF was not produced by html_to_pdf.py")
    return pdf_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:60] or "swms"


def resolve_out_dir(args, project_folder: Path, ctx: dict):
    """Decide where to save. Returns (out_dir Path, location_label str)."""
    if args.test:
        out = WORKSPACE / "outputs" / "automation" / "swms" / "TEST_RUN"
        out.mkdir(parents=True, exist_ok=True)
        return out, "TEST workspace folder (S: drive untouched)"
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        return out, f"override directory: {out}"
    if project_folder is not None:
        whs = project_folder / WHS_FOLDER_NAME
        if whs.exists():
            return whs, f'project "15 WHS and Environmental" folder'
    # No S: drive (e.g. the VPS): save into the project's own outputs folder,
    # not S:. This is where the Telegram brain saves on the VPS.
    if _projects is not None:
        try:
            out = _projects.resolve(ctx["project_number"])["out_dir"] / "swms"
            out.mkdir(parents=True, exist_ok=True)
            return out, f"project outputs folder (no S: drive): {out}"
        except Exception:  # noqa: BLE001
            pass
    out = WORKSPACE / "outputs" / "automation" / "swms"
    out.mkdir(parents=True, exist_ok=True)
    return out, "workspace fallback (WHS folder not found - not guessing a live path)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate a Dunsteel SWMS (HTML + PDF).")
    parser.add_argument("project", help="Project number, e.g. 501")
    parser.add_argument("activity", help="Free-text activity description")
    parser.add_argument("--out-dir", default=None, help="Override save directory")
    parser.add_argument("--test", action="store_true",
                        help="Write to a workspace temp folder; never touch the S: drive")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF conversion")
    args = parser.parse_args()

    number = args.project.strip()
    activity_description = args.activity.strip()
    today = datetime.date.today().isoformat()

    print(f"SWMS generator | project {number} | activity: {activity_description}")

    project_folder = find_project_folder(number)
    if project_folder is None:
        print(f"  WARNING: no S: drive folder matching '{number} -*' under {S_CURRENT_PROJECTS}")
    else:
        print(f"  Project folder: {project_folder}")

    ctx = build_project_context(number, project_folder)
    print(f"  Context: {ctx['project_name'] or '(unknown)'} | builder: {ctx['builder'] or '(unknown)'}")

    payload = generate_payload_via_claude(ctx, activity_description, today)
    if payload is None:
        print("  ANTHROPIC_API_KEY not set (or call failed) - using built-in fallback payload.")
        payload = fallback_payload(ctx, activity_description, today)
        used = "fallback"
    else:
        print(f"  Payload generated via {MODEL}.")
        used = "claude"

    # Render HTML
    out_dir, location_label = resolve_out_dir(args, project_folder, ctx)
    base = f"SWMS-{number}-{slugify(activity_description)}-Rev{payload.get('revision', '1')}"
    html_path = out_dir / f"{base}.html"
    html_path.write_text(render_html(payload, ctx), encoding="utf-8")
    print(f"  HTML saved: {html_path}")

    pdf_path = None
    if not args.no_pdf:
        pdf_path = convert_to_pdf(html_path)
        print(f"  PDF saved: {pdf_path}")

    print("\nDONE.")
    print(f"  Payload source: {used}")
    print(f"  Save location: {location_label}")
    print(f"  HTML: {html_path}")
    if pdf_path:
        print(f"  PDF:  {pdf_path}")


if __name__ == "__main__":
    main()
