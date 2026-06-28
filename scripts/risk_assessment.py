#!/usr/bin/env python3
"""
Generate a Dunsteel Risk Assessment (HTML + PDF) from a project number and a
free-text activity description. Sister tool to scripts/swms.py - it reuses that
script's project lookup, .env loader, PDF conversion and slug helpers, and adds
a Risk-Assessment-specific schema, Claude prompt, HTML renderer and fallback.

The HTML matches the Dunsteel RA house style (white header + logo, project
details, task description, supporting docs, hazardous materials, consultation,
high risk work checklist, qualifications / plant / PPE / permits, risk matrix,
hierarchy of controls, per-activity hazard-control tables with bulleted controls
and narrow risk columns, worker acknowledgment sign-off).

Usage:
    python scripts/risk_assessment.py <project> "<activity>"
    python scripts/risk_assessment.py 501 "Stair 2 handrail and SHS install from a 60t crane man box, hot works" --out-dir "C:/tmp"
    python scripts/risk_assessment.py 501 "..." --test       # never writes to S:
    python scripts/risk_assessment.py 501 "..." --no-pdf

Run from the workspace root.
"""
import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path

# Reuse the SWMS plumbing (project lookup, .env loader, PDF, slug, context).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import swms  # noqa: E402
try:
    import dunsteel_projects as _projects  # noqa: E402
except Exception:  # noqa: BLE001
    _projects = None

WORKSPACE = swms.WORKSPACE
WHS_FOLDER_NAME = swms.WHS_FOLDER_NAME
LOGO_PATH = WORKSPACE / "reference" / "assets" / "logos" / "dunsteel-logo-letterhead.jpg"
FALLBACK_DIR = WORKSPACE / "outputs" / "automation" / "risk-assessments"


def logo_data_uri() -> str:
    """Embed the Dunsteel logo as a base64 data URI so the RA renders the logo
    regardless of where the HTML is saved (project WHS folder, S: drive, etc.)."""
    try:
        import base64
        data = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Claude payload generation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a WHS document specialist preparing a Risk Assessment for Dunsteel Group, an Australian structural steel fabrication and installation company. You produce content that matches Australian WHS Regulation requirements and Dunsteel's existing Risk Assessment structure.

You output ONLY a single JSON object that conforms exactly to the schema below. No prose, no markdown fences, no commentary.

Schema (every key required):
{
  "ra_name": str,                  // short title, e.g. "Stair 2 Handrail & SHS Installation (Man Box)"
  "subtitle": str,                 // one line under the title, e.g. "Crane-Suspended Man Box"
  "date_label": str,               // e.g. "June 2026"
  "site_supervisor": str,          // named site supervisor, or "" if unknown
  "task_description": str,         // 2-4 sentences describing the scope and method
  "supporting_docs": [str],        // 4-7 referenced documents
  "hazardous_materials": str,      // 1-2 sentences, or "None identified for this activity."
  "consultation": str,             // how the RA was developed in consultation with workers
  "hrcw": [ {"checked": bool, "label": str} ],  // the standard high risk work categories below
  "qualifications": [str],         // tickets / licences / training required
  "plant": [str],                  // plant and equipment used
  "ppe": [str],                    // PPE required
  "permits": [str],                // permits required
  "activities": [
    {
      "name": str,                 // numbered activity, e.g. "1. Site Establishment & Crane Setup"
      "rows": [
        {
          "hrcw": str,             // related HRCW category, or "-" if none
          "step": str,             // the work step
          "hazards": str,          // the hazard arising
          "initial": str,          // initial risk, e.g. "VH25", "H16", "M9", "L4"
          "controls": [str],       // LIST of control points (each becomes its own bullet line)
          "residual": str          // residual risk after controls (lower than initial)
        }
      ]
    }
  ],
  "footer_note": str
}

Standard high risk work categories (use these exact labels, tick the relevant ones):
1. Risk of falls from greater than 2 metres
2. Work in an area with movement of powered mobile plant
3. Work involving use of a crane / lifting operations
4. Hot works - work involving a risk of fire (welding and grinding)
5. Work in or near a confined space
6. Work near live electrical installations
7. Work near live traffic (road or railway)
8. Demolition of a load-bearing structure

Risk rating convention: a letter band (L/M/H/VH) followed by a risk score number, e.g. "VH25", "H16", "M9", "L4". Residual ratings must be lower than initial after controls. The fixed risk matrix used by Dunsteel maps Likelihood (1-5) x Consequence (1-5) to these bands.

"controls" MUST be a list of short, specific control points (3-4 per row), each following the hierarchy of control and referencing real plant, tickets and Australian Standards where relevant (AS 1891.4 harnesses, AS 2550.10 EWP, AS/NZS 1554 welding, etc.). Produce 5-8 activities covering the full work sequence (site establishment, crane / man box, the core task, hot works if any, dropped objects, emergency rescue as relevant). Each activity has 1-3 rows.

Match the controls to the method described - do NOT invent controls that contradict the stated method (for example, do not add tag lines if the method does not use them).

Do not use long dashes anywhere. Use a hyphen, a colon, or restructure the sentence."""


def build_user_prompt(ctx: dict, activity: str, today: str) -> str:
    return f"""Produce the Risk Assessment JSON for the following:

Company performing the work: Dunsteel Group
Project number: {ctx['project_number']}
Project name: {ctx['project_name']}
Head contractor (builder): {ctx['builder']}
Location: {ctx['location']}
Date label: {today}

Activity / scope and proposed method (free text from the project coordinator):
{activity}

Generate the complete Risk Assessment as a single JSON object per the schema. Tailor the HRCW checklist, hazards, controls, PPE, plant, permits, qualifications and consultation to this specific activity and method, and to structural steel work practices."""


def generate_payload_via_claude(ctx: dict, activity: str, today: str):
    swms.load_dotenv()
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import claude_max
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        return claude_max.complete_json(
            build_user_prompt(ctx, activity, today), system=SYSTEM_PROMPT, model="sonnet")
    except Exception as e:  # noqa: BLE001 - fall back to the deterministic sample
        print(f"  Claude (Max) call failed ({e}); using fallback payload.")
        return None


def fallback_payload(ctx: dict, activity: str, today: str) -> dict:
    """Deterministic, generic structural-steel-at-height RA so the render / PDF
    pipeline runs offline. Always review and tailor before issue."""
    return {
        "ra_name": (f"{ctx['project_name']} - {activity}".strip(" -"))[:90],
        "subtitle": "Structural Steel Installation",
        "date_label": today,
        "site_supervisor": "",
        "task_description": (
            f"Structural steel works at {ctx['location'] or ctx['project_name']} for "
            f"{ctx['builder'] or 'the principal contractor'}. Scope: {activity}. Works include site "
            "establishment, access setup, the nominated steel activity, hot works as required, and progressive "
            "housekeeping and edge protection. This is a draft and must be reviewed and signed off by a competent "
            "person before use."
        ),
        "supporting_docs": [
            "Project Drawings and Specifications",
            "Relevant Installation Methodology",
            "Lifting Gear Register",
            "Site-Specific Induction",
            "Relevant Safety Data Sheets (SDS)",
        ],
        "hazardous_materials": "Welding fumes and grinding sparks where hot works apply - refer to SDS and hot works controls.",
        "consultation": (
            "This Risk Assessment has been developed in consultation with workers and their supervisor. Persons "
            "consulted: Site Supervisor, Project Manager, Principal Contractor representative."
        ),
        "hrcw": [
            {"checked": True,  "label": "Risk of falls from greater than 2 metres"},
            {"checked": True,  "label": "Work in an area with movement of powered mobile plant"},
            {"checked": True,  "label": "Work involving use of a crane / lifting operations"},
            {"checked": False, "label": "Hot works - work involving a risk of fire (welding and grinding)"},
            {"checked": False, "label": "Work in or near a confined space"},
            {"checked": False, "label": "Work near live electrical installations"},
            {"checked": False, "label": "Work near live traffic (road or railway)"},
            {"checked": False, "label": "Demolition of a load-bearing structure"},
        ],
        "qualifications": [
            "General Construction Induction Card (White Card) - all workers",
            "Working at Heights Certificate - all workers at height",
            "Rigger / Dogman tickets - rigging and lifting crew",
            "Crane Licence + WorkCover certificate - crane operator",
            "EWP / Scissor Lift Ticket - EWP operators",
        ],
        "plant": [
            "Mobile crane and rated lifting gear",
            "EWP / man box (certified, current SWL plate)",
            "Welding plant and angle grinder (where hot works apply)",
            "Hand and power tools (rated and tagged)",
        ],
        "ppe": [
            "Hard hat, steel-capped boots, long sleeves / pants, hi-vis",
            "Safety glasses",
            "Hearing protection",
            "Rigger's gloves on clip when rigging",
            "Safety harness + SRL or <=1.8m lanyard (AS 1891.4)",
            "Welding hood, gloves and leathers (hot works)",
        ],
        "permits": [
            "Hot Works Permit (where applicable)",
            "Working at Heights Permit (if required by principal contractor)",
            "Site induction",
        ],
        "activities": [
            {"name": "1. Site Establishment & Access", "rows": [
                {"hrcw": "Movement of powered mobile plant", "step": "Establish work area and set up plant",
                 "hazards": "Collision with workers or plant", "initial": "H16",
                 "controls": ["Work area delineated with barriers and signage.",
                              "Non-essential personnel removed from the operating zone.",
                              "Spotter and radio communication; reverse beeper and flashing light operational."],
                 "residual": "M8"},
            ]},
            {"name": "2. Working at Height", "rows": [
                {"hrcw": "Falls from greater than 2 metres", "step": "Work at height on the structure",
                 "hazards": "Fall from height; dropped objects", "initial": "VH20",
                 "controls": ["100% tie-off to a rated anchor at all times above 2m.",
                              "Tools tethered; exclusion / drop zone barricaded below.",
                              "Fall arrest gear inspected before use."],
                 "residual": "M10"},
            ]},
            {"name": "3. Emergency Rescue", "rows": [
                {"hrcw": "Falls from greater than 2 metres", "step": "Worker incapacitated or suspended at height",
                 "hazards": "Delayed rescue; suspension trauma", "initial": "H15",
                 "controls": ["Rescue plan briefed before works commence.",
                              "Rescue means (EWP / man box lowering) ready and crewed.",
                              "First aider on site; call 000 if a worker is unconscious."],
                 "residual": "M10"},
            ]},
        ],
        "footer_note": "Draft Risk Assessment - review, tailor and sign off by a competent person before use.",
    }


# ---------------------------------------------------------------------------
# HTML render (Dunsteel RA house style)
# ---------------------------------------------------------------------------

def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def risk_class(code: str) -> str:
    code = (code or "").upper()
    for k in ("VH", "H", "M", "L"):
        if code.startswith(k):
            return k.lower()
    return "low" if False else "low"


def _risk_cls(code: str) -> str:
    c = (code or "").upper()
    if c.startswith("VH"):
        return "vh"
    if c.startswith("H"):
        return "high"
    if c.startswith("M"):
        return "med"
    return "low"


def render_html(p: dict, ctx: dict) -> str:
    def li_list(items):
        return "".join(f"<li>{esc(x)}</li>" for x in items)

    hrcw_rows = "".join(
        f'<tr><td class="tick {"checked" if h.get("checked") else "unchecked"}">'
        f'{"&#9745;" if h.get("checked") else "&#9744;"}</td><td>{esc(h.get("label"))}</td></tr>'
        for h in p.get("hrcw", [])
    )

    activity_blocks = []
    for act in p.get("activities", []):
        rows_html = []
        for i, r in enumerate(act.get("rows", [])):
            alt = " class=\"alt\"" if i % 2 == 1 else ""
            controls = r.get("controls", [])
            if isinstance(controls, str):
                controls = [controls]
            ctrl_html = "<ul>" + "".join(f"<li>{esc(c)}</li>" for c in controls) + "</ul>"
            ir, rr = r.get("initial", ""), r.get("residual", "")
            rows_html.append(
                f"<tr{alt}>"
                f"<td>{esc(r.get('hrcw','-'))}</td>"
                f"<td>{esc(r.get('step'))}</td>"
                f"<td>{esc(r.get('hazards'))}</td>"
                f'<td class="risk {_risk_cls(ir)}">{esc(ir)}</td>'
                f'<td class="controls">{ctrl_html}</td>'
                f'<td class="risk {_risk_cls(rr)}">{esc(rr)}</td>'
                f"</tr>"
            )
        activity_blocks.append(
            '<div class="activity-block"><table class="hazard-table"><thead>'
            f'<tr><th colspan="6" class="activity-header">{esc(act.get("name"))}</th></tr>'
            '<tr class="col-headers">'
            '<th style="width:15%">High Risk Work Category</th>'
            '<th style="width:16%">Work Step</th>'
            '<th style="width:19%">Associated / Identified Hazards</th>'
            '<th style="width:5%">Initial</th>'
            '<th style="width:40%">Risk / Hazard Controls</th>'
            '<th style="width:5%">Revised</th></tr></thead><tbody>'
            + "".join(rows_html) + "</tbody></table></div>"
        )

    matrix = """
    <table class="matrix-table" style="width:70%; margin-bottom:6px;">
      <tr><th style="background:#1a2e4a;color:#fff;text-align:left;">Likelihood \\ Consequence</th>
      <th style="background:#1a2e4a;color:#fff;">Insignificant (1)</th><th style="background:#1a2e4a;color:#fff;">Minor (2)</th>
      <th style="background:#1a2e4a;color:#fff;">Moderate (3)</th><th style="background:#1a2e4a;color:#fff;">Major (4)</th>
      <th style="background:#1a2e4a;color:#fff;">Severe (5)</th></tr>
      <tr><td class="row-label">Almost Certain (5)</td><td class="med">M5</td><td class="med">M10</td><td class="high">H15</td><td class="vh">VH20</td><td class="vh">VH25</td></tr>
      <tr><td class="row-label">Likely (4)</td><td class="low">L4</td><td class="med">M8</td><td class="high">H12</td><td class="high">H16</td><td class="vh">VH20</td></tr>
      <tr><td class="row-label">Possible (3)</td><td class="low">L3</td><td class="med">M6</td><td class="med">M9</td><td class="high">H12</td><td class="high">H15</td></tr>
      <tr><td class="row-label">Unlikely (2)</td><td class="low">L2</td><td class="low">L4</td><td class="med">M6</td><td class="med">M8</td><td class="med">M10</td></tr>
      <tr><td class="row-label">Rare (1)</td><td class="low">L1</td><td class="low">L2</td><td class="low">L3</td><td class="low">L4</td><td class="low">L5</td></tr>
    </table>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Risk Assessment - {esc(p.get('ra_name'))} - {esc(ctx['project_name'])}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:Arial, sans-serif; font-size:11px; color:#222; background:#fff; }}
.page {{ width:210mm; min-height:297mm; margin:0 auto; padding:14mm 16mm; }}
.doc-header {{ display:flex; justify-content:space-between; align-items:flex-start; border-bottom:2px solid #1a2e4a; padding-bottom:12px; margin-bottom:14px; }}
.header-left .doc-type {{ font-size:17px; font-weight:bold; color:#1a2e4a; }}
.header-left .doc-sub {{ font-size:10.5px; color:#444; margin-top:3px; line-height:1.6; }}
.header-right {{ text-align:right; }}
.header-right img.logo {{ height:50px; width:auto; }}
.header-right .doc-date {{ font-size:9px; color:#666; margin-top:6px; }}
.section-heading {{ font-size:10px; font-weight:bold; color:#1a2e4a; text-transform:uppercase; letter-spacing:0.4px; background:#eef1f5; padding:5px 8px; margin:13px 0 6px 0; border-left:3px solid #1a2e4a; }}
table {{ width:100%; border-collapse:collapse; margin-bottom:8px; font-size:10px; }}
th, td {{ border:1px solid #c8cdd4; padding:4px 6px; vertical-align:top; }}
th {{ background:#1a2e4a; color:#fff; font-size:9px; font-weight:bold; text-align:center; }}
tr.alt td {{ background:#f7f8fa; }}
.details-table td.label {{ background:#eef1f5; font-weight:bold; width:18%; }}
.details-table td.value {{ width:32%; }}
.hrcw-table td.tick {{ text-align:center; font-size:13px; width:40px; }}
.hrcw-table td.tick.checked {{ color:#1a5e1a; font-weight:bold; }}
.hrcw-table td.tick.unchecked {{ color:#aaa; }}
.grid-table th {{ background:#d0d7e3; color:#1a2e4a; font-size:9px; text-align:left; padding:4px 8px; }}
.grid-table ul {{ padding-left:14px; margin:2px 0; }}
.grid-table li {{ margin-bottom:2px; }}
.matrix-table td, .matrix-table th {{ text-align:center; font-size:9px; font-weight:bold; }}
.matrix-table td.row-label {{ text-align:left; background:#eef1f5; font-weight:bold; }}
.matrix-table .vh {{ background:#e53935; color:#fff; }} .matrix-table .high {{ background:#ef6c00; color:#fff; }}
.matrix-table .med {{ background:#f9a825; color:#222; }} .matrix-table .low {{ background:#558b2f; color:#fff; }}
.risk-key {{ display:flex; gap:6px; margin-bottom:10px; }}
.risk-key-item {{ flex:1; padding:5px 8px; border-radius:2px; font-size:9px; font-weight:bold; }}
.risk-key-item.vh {{ background:#e53935; color:#fff; }} .risk-key-item.high {{ background:#ef6c00; color:#fff; }}
.risk-key-item.med {{ background:#f9a825; color:#222; }} .risk-key-item.low {{ background:#558b2f; color:#fff; }}
.risk-key-item span {{ font-weight:normal; font-size:8px; display:block; }}
.activity-block {{ margin-bottom:10px; page-break-inside:avoid; }}
.hazard-table {{ font-size:9.5px; }}
.hazard-table th.activity-header {{ background:#1a2e4a; color:#fff; font-size:10px; text-align:left; padding:5px 8px; }}
.hazard-table tr.col-headers th {{ background:#d0d7e3; color:#1a2e4a; font-size:8.5px; }}
.hazard-table td.risk {{ text-align:center; font-weight:bold; font-size:8.5px; padding:4px 2px; }}
.hazard-table td.risk.vh {{ background:#e53935; color:#fff; }} .hazard-table td.risk.high {{ background:#ef6c00; color:#fff; }}
.hazard-table td.risk.med {{ background:#f9a825; color:#222; }} .hazard-table td.risk.low {{ background:#558b2f; color:#fff; }}
.hazard-table td.controls {{ font-size:9px; }}
.hazard-table td.controls ul {{ margin:0; padding-left:13px; }}
.hazard-table td.controls li {{ margin-bottom:4px; }}
.signoff-table th {{ background:#1a2e4a; color:#fff; text-align:left; }}
.signoff-table td {{ height:26px; }}
.doc-footer {{ margin-top:18px; padding-top:8px; border-top:1px solid #ccc; font-size:8px; color:#888; text-align:center; }}
@media print {{ body {{ font-size:9px; }} .page {{ padding:10mm 12mm; width:100%; }} }}
</style></head><body><div class="page">

<div class="doc-header">
  <div class="header-left">
    <div class="doc-type">RISK ASSESSMENT</div>
    <div class="doc-sub">{esc(p.get('ra_name'))}{(' - ' + esc(p.get('subtitle'))) if p.get('subtitle') else ''}<br>
      Project {esc(ctx['project_number'])} - {esc(ctx['project_name'])} | Principal Contractor: {esc(ctx['builder'])}</div>
  </div>
  <div class="header-right">
    <img class="logo" src="{logo_data_uri()}" alt="Dunsteel - Solutions in Steel">
    <div class="doc-date">Date: {esc(p.get('date_label'))}</div>
  </div>
</div>

<div class="section-heading">Project Details</div>
<table class="details-table">
  <tr><td class="label">Company</td><td class="value">Dunsteel Group</td><td class="label">Principal Contractor</td><td class="value">{esc(ctx['builder'])}</td></tr>
  <tr><td class="label">Project Manager</td><td class="value">{esc(swms.DUNSTEEL['contact_name'])}</td><td class="label">Project</td><td class="value">{esc(ctx['project_number'])} - {esc(ctx['project_name'])}</td></tr>
  <tr><td class="label">PM Phone</td><td class="value">{esc(swms.DUNSTEEL['contact_phone'])}</td><td class="label">Location</td><td class="value">{esc(ctx['location'])}</td></tr>
  <tr><td class="label">Site Supervisor</td><td class="value">{esc(p.get('site_supervisor'))}</td><td class="label">Authorised By</td><td class="value">{esc(swms.DUNSTEEL['contact_name'])}</td></tr>
</table>

<div class="section-heading">Task / Project Description</div>
<table><tr><td style="padding:6px 8px;">{esc(p.get('task_description'))}</td></tr></table>

<div class="section-heading">Supporting Documents</div>
<table><tr><td style="padding:6px 8px;"><ul style="padding-left:16px;">{li_list(p.get('supporting_docs', []))}</ul></td></tr></table>

<div class="section-heading">Hazardous Materials</div>
<table><tr><td style="padding:6px 8px;">{esc(p.get('hazardous_materials'))}</td></tr></table>

<div class="section-heading">Consultation</div>
<table><tr><td style="padding:6px 8px;">{esc(p.get('consultation'))}</td></tr></table>

<div class="section-heading">High Risk Work</div>
<table class="hrcw-table">{hrcw_rows}</table>

<div class="section-heading">Qualifications, Plant &amp; PPE</div>
<table class="grid-table">
  <tr><th style="width:50%">Qualifications / VOC Required</th><th style="width:50%">Plant and Equipment Required</th></tr>
  <tr><td><ul>{li_list(p.get('qualifications', []))}</ul></td><td><ul>{li_list(p.get('plant', []))}</ul></td></tr>
  <tr><th>Personal Protective Equipment</th><th>Permits Required</th></tr>
  <tr><td><ul>{li_list(p.get('ppe', []))}</ul></td><td><ul>{li_list(p.get('permits', []))}</ul></td></tr>
</table>

<div class="section-heading">Risk Analysis Matrix</div>
{matrix}
<div class="risk-key">
  <div class="risk-key-item vh">VH - Very High <span>Immediate action. Stop work.</span></div>
  <div class="risk-key-item high">H - High <span>Senior management attention required.</span></div>
  <div class="risk-key-item med">M - Medium <span>Management responsibility specified.</span></div>
  <div class="risk-key-item low">L - Low <span>Manage by routine procedures.</span></div>
</div>

<div class="section-heading">Hazard Controls</div>
{''.join(activity_blocks)}

<div class="section-heading">Worker Acknowledgment</div>
<p style="margin-bottom:8px;font-size:10px;font-style:italic;">All workers involved in this work must sign below to confirm they have read, understood, and agree to comply with this Risk Assessment.</p>
<table class="signoff-table">
  <tr><th style="width:30%">Name</th><th style="width:20%">Company</th><th style="width:30%">Signature</th><th style="width:20%">Date</th></tr>
  {''.join('<tr><td></td><td></td><td></td><td></td></tr>' for _ in range(8))}
</table>

<div class="doc-footer">{esc(p.get('footer_note'))} &nbsp;|&nbsp; Document prepared by Dunsteel Group &nbsp;|&nbsp; Project {esc(ctx['project_number'])} - {esc(ctx['project_name'])} &nbsp;|&nbsp; {esc(p.get('date_label'))}</div>

</div></body></html>"""


# ---------------------------------------------------------------------------
# Save location + main
# ---------------------------------------------------------------------------

def resolve_out_dir(args, project_folder):
    """Decide where to save. Returns (Path, label)."""
    if args.test:
        d = FALLBACK_DIR / "TEST_RUN"
        return d, "workspace test folder (S: not touched)"
    if args.out_dir:
        return Path(args.out_dir), "override directory"
    if project_folder is not None:
        whs = project_folder / WHS_FOLDER_NAME
        if whs.exists():
            return whs, 'project "15 WHS and Environmental" folder'
    # No S: drive (e.g. the VPS): save into the project's own outputs folder.
    if _projects is not None:
        try:
            out = _projects.resolve(args.project)["out_dir"] / "risk-assessments"
            return out, f"project outputs folder (no S: drive): {out}"
        except Exception:  # noqa: BLE001
            pass
    return FALLBACK_DIR, "workspace fallback (project WHS folder not found)"


def main():
    parser = argparse.ArgumentParser(description="Generate a Dunsteel Risk Assessment (HTML + PDF).")
    parser.add_argument("project", help="Project number, e.g. 501")
    parser.add_argument("activity", help="Free-text activity / method description")
    parser.add_argument("--out-dir", default=None, help="Override save directory")
    parser.add_argument("--test", action="store_true", help="Write to workspace test folder; never touch S:")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF conversion")
    args = parser.parse_args()

    swms.load_dotenv()
    today = datetime.date.today().strftime("%B %Y")

    project_folder = swms.find_project_folder(args.project)
    ctx = swms.build_project_context(args.project, project_folder)

    payload = generate_payload_via_claude(ctx, args.activity, today)
    source = "Claude"
    if payload is None:
        payload = fallback_payload(ctx, args.activity, today)
        source = "built-in fallback (set ANTHROPIC_API_KEY for a tailored RA)"

    html_str = render_html(payload, ctx)
    if "—" in html_str:
        html_str = html_str.replace("—", "-")  # enforce no long dashes

    out_dir, location_label = resolve_out_dir(args, project_folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"RA-{args.project}-{swms.slugify(payload.get('ra_name') or args.activity)}"
    html_path = out_dir / f"{base}.html"
    html_path.write_text(html_str, encoding="utf-8")
    print(f"HTML saved: {html_path}")

    if not args.no_pdf:
        try:
            pdf_path = swms.convert_to_pdf(html_path)
            print(f"PDF saved:  {pdf_path}")
        except Exception as e:
            print(f"PDF conversion failed ({e}). HTML is still available.")

    print(f"Save location: {location_label}")
    print(f"Payload source: {source}")
    print("Note: this is a DRAFT. Review, tailor and have a competent person sign off before issue.")


if __name__ == "__main__":
    main()
