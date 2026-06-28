#!/usr/bin/env python3
"""
safety_pdf_dispatcher.py - Surface 2, VPS cron (replaces the n8n flow).

Polls the Monday Toolbox Talk + Safety Audit boards for NEW form submissions
(items where the new `PM` single-select is set and `Automation Status` is still
empty), renders a Dunsteel-branded PDF from the submitted fields, emails it to the
selected PM (CC Casey), and writes `Automation Status = PDF Sent` back so the item
is never processed twice.

This is a plain cron+script (per the Dunsteel VPS convention): linear, no n8n.
It reuses the same templates and `html_to_pdf.py` render path as Surface 1.

Sender resolution (first available wins):
  1. Microsoft Graph app-only sendMail as nathanh - if the app reg carries Mail.Send.
  2. Gmail SMTP - if GMAIL_USER + GMAIL_APP_PASSWORD are in .env.
  3. Dry-run - no send, no writeback (items stay pending). Logged.

Usage (on the VPS):
    python3 scripts/safety_pdf_dispatcher.py            # dry-run: report pending, render, no send/writeback
    python3 scripts/safety_pdf_dispatcher.py --live     # send + writeback
    python3 scripts/safety_pdf_dispatcher.py --board toolbox --limit 5

HARD RULE: no long dashes anywhere in this file or generated content.
"""

import argparse
import base64
import html
import json
import os
import smtplib
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))
import dunsteel_projects as projects  # noqa: E402
from dunsteel_monday import MondayClient  # noqa: E402

REF = WORKSPACE / "reference" / "systems" / "toolbox-talks"
LOGO = WORKSPACE / "reference" / "assets" / "logos" / "dunsteel-logo-letterhead.jpg"
HTML_TO_PDF = WORKSPACE / "scripts" / "html_to_pdf.py"
CC_EMAIL = os.environ.get("DISPATCH_CC", "")  # no CC by default; set DISPATCH_CC="someone@dunsteel.com.au" to add one
SENDER_MAILBOX = "nathanh@dunsteel.com.au"
GRAPH_TENANT = "YOUR_PROJECT_EVENTS_DB_ID"

BOARDS = {
    "toolbox": {
        "board": YOUR_TOOLBOX_BOARD_ID, "template": REF / "toolbox-talk-template.html",
        "pm_col": "single_select82oifav", "status_col": "color_mm41m213",
        "job_col": "single_select", "date_col": "date",
        "doc_kind": "Toolbox Talk",
    },
    "audit": {
        "board": YOUR_SAFETY_AUDIT_BOARD_ID, "template": REF / "safety-audit-template.html",
        "pm_col": "single_selectb6ar7yk", "status_col": "color_mm41qb2d",
        "job_col": "single_select", "date_col": "date",
        "doc_kind": "Pre-Start Safety Audit",
    },
}
# Toolbox attendee columns in form order: (name text col, signature file col).
ATT_PAIRS = [
    ("short_text64", "signature0"), ("short_text2", "signature04"), ("short_text1", "signature24"),
    ("short_text99", "signature4"), ("short_text93", "signature5"), ("short_text16", "signature9"),
    ("short_text20", "signature30"), ("short_text90", "signature8"), ("short_text8", "signature1"),
]

AUDIT_CHECKS = [
    ("Qualification adequate for work activities", "single_select2"),
    ("All permits in place", "single_select4"),
    ("Log books filled out", "single_select6"),
    ("Correct and adequate PPE", "single_select58"),
    ("Electrical tags up to date", "single_select5"),
    ("Materials and tools stored safely", "single_select0"),
    ("Lead hooks", "single_select06"),
]


def log(msg):
    print(msg, flush=True)


def load_dotenv():
    env = WORKSPACE / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


# --------------------------------------------------------------------------- #
# Monday read
# --------------------------------------------------------------------------- #
def fetch_pending(m: MondayClient, cfg: dict, limit: int, window_mins: int):
    """New form submissions: created within the last `window_mins`, PM set, and
    Automation Status still empty. The time window is the de-dupe that keeps the
    65/109 historical items (all created long ago) out, even though they may carry
    a default PM value; the status writeback prevents re-send inside the window."""
    import datetime
    q = ("query($b:[ID!]){boards(ids:$b){items_page(limit:%d,"
         "query_params:{order_by:[{column_id:\"__creation_log__\",direction:desc}]}){"
         "items{id name created_at column_values{id text}}}}}" % max(limit * 2, 50))
    data = m.query(q, {"b": [str(cfg["board"])]})
    boards = data.get("boards") or []
    items = boards[0]["items_page"]["items"] if boards else []
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for it in items:
        created = it.get("created_at", "")
        try:
            cdt = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            continue
        age_min = (now - cdt).total_seconds() / 60.0
        if age_min > window_mins:
            break  # ordered desc, so everything after is older too
        cv = {c["id"]: (c.get("text") or "") for c in it["column_values"]}
        pm = cv.get(cfg["pm_col"], "").strip()
        status = cv.get(cfg["status_col"], "").strip()
        if pm and not status:
            out.append({"id": it["id"], "name": it["name"], "cv": cv, "pm": pm})
        if len(out) >= limit:
            break
    return out


def project_number(job_text: str):
    digits = ""
    for ch in job_text.strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def _fill(template: Path, repl: dict) -> str:
    out = template.read_text(encoding="utf-8")
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def fetch_signatures(m: MondayClient, item_id: str, sig_cols: list) -> dict:
    """Return {sig_col_id: data-uri} for any signature uploads on the item."""
    q = ("query($i:[ID!]){items(ids:$i){column_values(ids:%s){id value}}}" % json.dumps(sig_cols))
    try:
        cvs = m.query(q, {"i": [str(item_id)]})["items"][0]["column_values"]
    except Exception:
        return {}
    asset_of = {}
    for c in cvs:
        if not c.get("value"):
            continue
        try:
            for f in json.loads(c["value"]).get("files", []):
                if f.get("assetId"):
                    asset_of[c["id"]] = f["assetId"]
                    break
        except Exception:
            pass
    if not asset_of:
        return {}
    ids = list({str(a) for a in asset_of.values()})
    try:
        assets = {str(a["id"]): (a.get("public_url") or a.get("url"))
                  for a in m.query("query($a:[ID!]!){assets(ids:$a){id url public_url}}", {"a": ids})["assets"]}
    except Exception:
        return {}
    out = {}
    for col, aid in asset_of.items():
        url = assets.get(str(aid))
        if not url:
            continue
        try:
            data = urllib.request.urlopen(urllib.request.Request(url), timeout=20).read()
            out[col] = "data:image/png;base64," + base64.b64encode(data).decode()
        except Exception:
            pass
    return out


def _split_bullets(raw: str) -> list:
    """Deterministic fallback: break text into bullet points on line breaks, or on
    sentence/semicolon boundaries if it is one run-on blob."""
    import re
    raw = (raw or "").replace("\r", "").strip()
    if not raw:
        return []
    parts = [p.strip(" .\t-*") for p in raw.split("\n") if p.strip()]
    if len(parts) <= 1:
        parts = [s.strip(" .\t-*") for s in re.split(r"(?<=[.;])\s+", raw) if s.strip()]
    return [p for p in parts if p]


def format_sections(topic: str, agenda: str, hazards: str, controls: str) -> dict:
    """Review agent: clean the toolbox fields into tidy bullet points so the PDF
    looks good even when the form was filled sloppily. Uses Claude when
    ANTHROPIC_API_KEY is set, otherwise a deterministic split. Never invents content."""
    fallback = {"agenda": _split_bullets(agenda), "hazards": _split_bullets(hazards),
                "controls": _split_bullets(controls)}
    prompt = (
        "You are formatting a site toolbox talk for a professional PDF. Reformat the fields "
        "below into clean, concise bullet points. Fix spacing, casing and run-on text; split "
        "distinct ideas into separate bullets; keep wording faithful and do NOT invent content. "
        "Australian English. No long dashes.\n\n"
        f"TOPIC: {topic}\n\nAGENDA / KEY POINTS:\n{agenda}\n\nHAZARDS:\n{hazards}\n\nCONTROLS:\n{controls}\n\n"
        'Return STRICT JSON only: {"agenda":[...],"hazards":[...],"controls":[...]} '
        "(each an array of short plain-text bullet strings; empty array if the field was blank).")
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        import claude_max
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        data = claude_max.complete_json(prompt, model="haiku")
        out = {k: [str(x).strip() for x in data.get(k, []) if str(x).strip()]
               for k in ("agenda", "hazards", "controls")}
        # if the model returned nothing useful for a field, fall back to the split
        for k in out:
            if not out[k]:
                out[k] = fallback[k]
        return out
    except Exception as e:
        print(f"  (format agent failed, using plain split: {e})")
        return fallback


def bullets_html(items: list) -> str:
    if not items:
        return "&nbsp;"
    lis = "".join(f'<li style="margin-bottom:5px">{html.escape(i)}</li>' for i in items)
    return f'<ul style="margin:0;padding-left:18px;line-height:1.7">{lis}</ul>'


def render_toolbox(cfg, item, proj):
    cv = item["cv"]
    sigs = item.get("signatures", {})
    sections = format_sections(cv.get("short_text9", ""), cv.get("long_text", ""),
                               cv.get("long_text5", ""), cv.get("long_text1", ""))
    points_html = "\n      ".join(f"<li>{html.escape(p)}</li>" for p in sections["agenda"]) or "<li>&nbsp;</li>"
    att_rows = []
    for name_col, sig_col in ATT_PAIRS:
        nm = (cv.get(name_col, "") or "").strip()
        has_sig = sig_col in sigs
        if not nm and not has_sig:
            continue  # render a row if there is a name OR a signature (keep all who signed)
        person, _, company = nm.partition(" - ")
        sig = (f'<img src="{sigs[sig_col]}" style="height:46px;max-width:100%;object-fit:contain">'
               if has_sig else "&nbsp;")
        att_rows.append(f"<tr><td>{html.escape(person.strip())}</td>"
                        f"<td>{html.escape(company.strip())}</td><td>{sig}</td></tr>")
    for _ in range(4):  # blank rows for walk-up attendees to sign on
        att_rows.append('<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>')
    rows = "\n".join(att_rows)
    conducted_by = cv.get("short_text", "").strip() or item.get("pm", "")
    repl = {
        "{{LOGO_PATH}}": str(LOGO.resolve()),
        "{{PROJECT_NUMBER}}": str(proj["number"]), "{{PROJECT_NAME}}": html.escape(proj["name"]),
        "{{HEAD_CONTRACTOR}}": html.escape(proj["head_contractor"] or "N/A"),
        "{{LOCATION}}": html.escape(proj["location"] or "N/A"),
        "{{DATE}}": html.escape(cv.get(cfg["date_col"], "")),
        "{{CONDUCTED_BY}}": html.escape(conducted_by), "{{CONDUCTED_BY_ROLE}}": "Project Manager",
        "{{TOPIC}}": html.escape(cv.get("short_text9", "") or "Weekly Toolbox Talk"),
        "{{POINTS_HTML}}": points_html,
        "{{HAZARDS}}": bullets_html(sections["hazards"]),
        "{{CONTROLS}}": bullets_html(sections["controls"]),
        "{{ATTENDEE_ROWS}}": rows,
    }
    return _fill(cfg["template"], repl)


def render_audit(cfg, item, proj):
    cv = item["cv"]
    rows = []
    for title, col in AUDIT_CHECKS:
        val = (cv.get(col, "") or "").strip().lower()
        badge = '<span class="badge-yes">YES</span>' if val == "yes" else (
            '<span class="badge-no">NO</span>' if val == "no" else "&nbsp;")
        rows.append(f"<tr><td>{html.escape(title)}</td><td>{badge}</td><td>&nbsp;</td></tr>")
    repl = {
        "{{LOGO_PATH}}": str(LOGO.resolve()),
        "{{PROJECT_NUMBER}}": str(proj["number"]), "{{PROJECT_NAME}}": html.escape(proj["name"]),
        "{{HEAD_CONTRACTOR}}": html.escape(proj["head_contractor"] or "N/A"),
        "{{SCOPE}}": html.escape(cv.get("short_text9", "") or cv.get("long_text", "")[:80] or "N/A"),
        "{{DATE}}": html.escape(cv.get(cfg["date_col"], "")),
        "{{PM_NAME}}": html.escape(item["pm"]),
        "{{CHECK_ROWS}}": "\n      ".join(rows),
        "{{TASKS}}": html.escape(cv.get("long_text", "")),
        "{{HAZARDS}}": html.escape(cv.get("long_text5", "")),
        "{{CONTROLS}}": html.escape(cv.get("long_text1", "")),
        "{{NOTES}}": html.escape(cv.get("long_text6", "")) or "&nbsp;",
    }
    return _fill(cfg["template"], repl)


def render_pdf(html_str: str, stem: str) -> Path:
    work = Path(tempfile.mkdtemp(prefix="safetypdf_"))
    hp = work / f"{stem}.html"
    hp.write_text(html_str, encoding="utf-8")
    subprocess.run([sys.executable, str(HTML_TO_PDF), str(hp)], check=True)
    return hp.with_suffix(".pdf")


# --------------------------------------------------------------------------- #
# Senders
# --------------------------------------------------------------------------- #
def graph_token():
    """App-only token for the nathanh mailbox; returns (token, roles) or (None, [])."""
    sec = cid = None
    inblk = False
    for raw in (WORKSPACE / ".env").read_text(encoding="utf-8").splitlines():
        l = raw.strip()
        if l.startswith("OUTLOOK_CREDENTIALS"):
            inblk = SENDER_MAILBOX in l
            continue
        if not inblk:
            continue
        if l.startswith("SECRET"):
            sec = l.partition("=")[2].strip()
        elif l.startswith("CLIENT_ID"):
            cid = l.partition("=")[2].strip()
    if not (cid and sec):
        return None, []
    data = urllib.parse.urlencode({
        "client_id": cid, "client_secret": sec,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://login.microsoftonline.com/{GRAPH_TENANT}/oauth2/v2.0/token", data=data, method="POST")
        tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]
    except Exception as e:
        log(f"  graph token error: {e}")
        return None, []
    p = tok.split(".")[1]
    p += "=" * (-len(p) % 4)
    roles = json.loads(base64.urlsafe_b64decode(p)).get("roles", [])
    return tok, roles


def resolve_sender():
    """Return ('gmail_oauth'|'graph'|'smtp'|'none', token-or-None)."""
    if os.environ.get("GMAIL_SEND_REFRESH_TOKEN"):
        return "gmail_oauth", None
    tok, roles = graph_token()
    if tok and "Mail.Send" in roles:
        return "graph", tok
    if os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"):
        return "smtp", None
    return "none", None


def gmail_oauth_send(to_email, subject, body, pdf: Path):
    """Send via the Gmail API using a stored refresh token (dunsteelautomations)."""
    data = urllib.parse.urlencode({
        "client_id": os.environ["GMAIL_SEND_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_SEND_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_SEND_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"), timeout=20).read())
    access = tok["access_token"]
    em = EmailMessage()
    em["From"] = os.environ.get("GMAIL_SENDER", "")
    em["To"] = to_email
    if CC_EMAIL:
        em["Cc"] = CC_EMAIL
    em["Subject"] = subject
    em.set_content(body)
    em.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(),
        headers={"Authorization": "Bearer " + access, "Content-Type": "application/json"},
        method="POST")
    urllib.request.urlopen(req, timeout=30)


def send_graph(token, to_email, subject, body, pdf: Path):
    msg = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
            "ccRecipients": ([{"emailAddress": {"address": CC_EMAIL}}] if CC_EMAIL else []),
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": pdf.name,
                "contentBytes": base64.b64encode(pdf.read_bytes()).decode(),
            }],
        }, "saveToSentItems": True,
    }
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/users/{SENDER_MAILBOX}/sendMail",
        data=json.dumps(msg).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    urllib.request.urlopen(req, timeout=30)


def send_smtp(to_email, subject, body, pdf: Path):
    em = EmailMessage()
    em["From"] = os.environ["GMAIL_USER"]
    em["To"] = to_email
    if CC_EMAIL:
        em["Cc"] = CC_EMAIL
    em["Subject"] = subject
    em.set_content(body)
    em.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(em)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually email + writeback")
    ap.add_argument("--board", choices=["toolbox", "audit", "both"], default="both")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--window-mins", type=int, default=25,
                    help="only process items created within this many minutes (de-dupe)")
    args = ap.parse_args(argv)

    m = MondayClient()
    mode, token = resolve_sender()
    if args.live and mode == "none":
        log("NO SEND CHANNEL configured (no Mail.Send, no GMAIL_APP_PASSWORD). "
            "Running render-only; items left pending. Add a credential to go live.")
    log(f"dispatcher: mode={mode} live={args.live} board={args.board}")

    boards = ["toolbox", "audit"] if args.board == "both" else [args.board]
    total_sent = total_pending = 0
    for key in boards:
        cfg = BOARDS[key]
        pending = fetch_pending(m, cfg, args.limit, args.window_mins)
        total_pending += len(pending)
        log(f"[{key}] {len(pending)} pending")
        for item in pending:
            num = project_number(item["cv"].get(cfg["job_col"], ""))
            proj = projects.resolve(num) if num else projects.resolve(501)
            pm_email = projects.pm_email(item["pm"])
            try:
                if key == "toolbox":
                    item["signatures"] = fetch_signatures(m, item["id"], [s for _, s in ATT_PAIRS])
                    html_str = render_toolbox(cfg, item, proj)
                else:
                    html_str = render_audit(cfg, item, proj)
                stem = f"{proj['number']}-{key}-{item['id']}"
                pdf = render_pdf(html_str, stem)
            except Exception as e:
                log(f"  item {item['id']}: render FAILED: {e}")
                continue
            subject = f"{cfg['doc_kind']} PDF - {proj['number']} {proj['name']} - {item['cv'].get(cfg['date_col'],'')}"
            body = (f"Hi {item['pm'].split()[0]},\n\nAttached is the {cfg['doc_kind'].lower()} for "
                    f"{proj['number']} {proj['name']}. Save or print it as you need.\n\nThanks")
            if not args.live or mode == "none":
                log(f"  item {item['id']} -> {pm_email}  [PDF {pdf.stat().st_size//1024}KB]  (no send)")
                continue
            try:
                if mode == "gmail_oauth":
                    gmail_oauth_send(pm_email, subject, body, pdf)
                elif mode == "graph":
                    send_graph(token, pm_email, subject, body, pdf)
                else:
                    send_smtp(pm_email, subject, body, pdf)
                m.change_column_values(cfg["board"], item["id"], {cfg["status_col"]: {"label": "PDF Sent"}})
                total_sent += 1
                log(f"  item {item['id']} -> emailed {pm_email}, marked PDF Sent")
            except Exception as e:
                log(f"  item {item['id']}: SEND FAILED ({e}); left pending")
    log(f"done: {total_sent} sent, {total_pending} pending seen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
