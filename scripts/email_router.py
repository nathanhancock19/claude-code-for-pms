#!/usr/bin/env python3
"""
email_router.py - Dunsteel multi-project email router (PIL Phase 1 / Agent K).

This is the ingest engine for the Project Intelligence Layer. It:
  1. Loads the per-project registry from reference/projects/*/project-brief.md
     (YAML header: project_id, keywords, contact_emails, head_contractor).
  2. Polls Nathan's Outlook inbox via Microsoft Graph (app-only auth, Mail.Read
     application permission with admin consent - confirmed working 2026-06-04).
  3. Pre-filters each message against the registry (privacy + cost control: only
     project-matching, corporate-domain mail reaches the classifier).
  4. Classifies each routed message with Claude (claude-sonnet-4-6): event_type,
     summary, key_facts, action_owner_hint, suggested_due_date, confidence.
  5. DRY-RUN (default): writes a human review report (markdown + raw JSON) to
     outputs/automation/wf-email-router-multi-project/ for accuracy scoring.
     LIVE (--live): writes Project Events rows to Notion and fires Telegram
     alerts for {Variation, RFI, Site Issue} at confidence >= 0.8.

Lifts onto the VPS as a cron job once the classifier accuracy is proven (per
the standing rule: scheduled jobs run as VPS cron + Python, not in-session).

Usage:
    python scripts/email_router.py                 # dry-run, last 7 days
    python scripts/email_router.py --days 30       # dry-run, last 30 days
    python scripts/email_router.py --days 7 --live # write to Notion + Telegram
    python scripts/email_router.py --no-classify   # routing only, skip Claude

Environment (self-loaded from workspace-root .env):
    OUTLOOK_CREDENTIALS nathanh@dunsteel.com.au block (SECRET + CLIENT_ID)
    ANTHROPIC_API_KEY
    NOTION_API_KEY (Dunsteel Projects Hub)   - only needed for --live
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID    - only needed for --live alerts

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROJECTS_DIR = WORKSPACE / "reference" / "projects"
OUT_DIR = WORKSPACE / "outputs" / "automation" / "wf-email-router-multi-project"

# Classification runs on the Claude Max subscription (claude -p via claude_max),
# not metered API credits. claude_max lives next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_max  # noqa: E402

# Public identifiers (not secrets). Tenant discovered from the OIDC endpoint for
# dunsteel.com.au; override via env if it ever changes.
TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "YOUR_PROJECT_EVENTS_DB_ID")
MAILBOX = "nathanh@dunsteel.com.au"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MODEL = "claude-sonnet-4-6"

# Notion Project Events DB (see reference/notion-databases.md). Used only in --live.
NOTION_EVENTS_DS = "YOUR_NOTION_PARENT_ID"

# Event types Claude is allowed to assign (must match the Notion select options).
EVENT_TYPES = [
    "Delivery", "Variation", "RFI", "Decision", "Schedule Change",
    "Finish Approval", "Site Issue", "Commercial", "General",
]
ALERT_TYPES = {"Variation", "RFI", "Site Issue"}
ALERT_CONFIDENCE = 0.8

# Marketing / SaaS-admin senders to drop before routing. Deliberately narrow:
# operational noreply mail that carries real events (Veyor delivery bookings,
# Hammertech permit approvals) is NOT here and still routes.
SUPPRESS_SENDERS = (
    "monday.com",
    "powerautomatenoreply@",
    "mssecurity-noreply@",
    "openai.com",
    "spotify.com",
    "sydneytools.com.au",
    "sales@supplier.example.com",
    "sales@supplier.example.com",
    "no-reply@vendor.example.com",
    "online@supplier.example.com",
    "customerexperience@client.example.com",
)

# Subject phrases that drop a message before routing regardless of sender.
# Subcontractor invoice reports (a labour-hire subcontractor / a rigging subcontractor) are owned end-to-end
# by WF5 invoice-allocator for project 501; they should NOT also land in the
# project event stream. Internal replies to these threads were previously routed
# to the wrong project on a stray job-number token (see phase1-accuracy report).
SUPPRESS_SUBJECTS = (
    "subcontractor report",
    # Procore auto-reminders (Parkline Builders etc.) re-sent constantly as new emails -
    # pure noise, the real action lives in Procore. Never ingest or alert.
    "overdue observation",
    "overdue defect",
)

# Generic single words that must not route a project on their own (too noisy).
GENERIC_WORDS = {
    "early", "works", "street", "works", "project", "steel", "stair", "stairs",
    "level", "site", "lane", "cove", "park", "hospital", "airport", "centre",
}


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def load_dotenv():
    """Populate os.environ from the workspace-root .env (zero dependency).

    Does not overwrite a value already in the real environment.
    """
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def read_outlook_creds():
    """Read SECRET + CLIENT_ID for the nathanh mailbox from the .env block.

    The .env stores two OUTLOOK_CREDENTIALS blocks (nathanh + admin) each with
    its own SECRET/CLIENT_ID lines, so this parses the named block explicitly
    rather than relying on key ordering. Only the nathanh app reg is admin
    consented for Mail.Read.
    """
    env_path = WORKSPACE / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    in_block = False
    secret = client_id = None
    for raw in lines:
        line = raw.strip()
        if line.startswith("OUTLOOK_CREDENTIALS"):
            in_block = MAILBOX in line
            continue
        if not in_block:
            continue
        if line.startswith("SECRET"):
            secret = line.partition("=")[2].strip()
        elif line.startswith("CLIENT_ID"):
            client_id = line.partition("=")[2].strip()
        elif line.startswith("OUTLOOK_CREDENTIALS"):
            break
    return client_id, secret


# --------------------------------------------------------------------------- #
# Project registry
# --------------------------------------------------------------------------- #
def parse_yaml_header(text):
    """Minimal YAML front-matter parser for the brief headers.

    Handles scalars and simple `- item` lists. Avoids a PyYAML dependency.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n")
    data = {}
    current_key = None
    for raw in block.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw.startswith("  ") and raw.strip().startswith("- "):
            if current_key:
                data.setdefault(current_key, [])
                if isinstance(data[current_key], list):
                    data[current_key].append(_unquote(raw.strip()[2:]))
            continue
        if ":" in raw:
            key, _, val = raw.partition(":")
            key = key.strip()
            val = val.strip()
            current_key = key
            if val == "" or val == "[]":
                data[key] = []          # list follows, or explicitly empty
            else:
                data[key] = _unquote(val)
    return data


def _unquote(s):
    return s.strip().strip('"').strip("'").strip()


def load_registry():
    """Build the project registry from every brief with a YAML header."""
    registry = []
    for brief in sorted(PROJECTS_DIR.glob("*/project-brief.md")):
        header = parse_yaml_header(brief.read_text(encoding="utf-8"))
        if not header.get("project_id"):
            continue
        pid = str(header["project_id"]).strip()
        keywords = header.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        emails = header.get("contact_emails") or []
        if isinstance(emails, str):
            emails = [emails]
        registry.append({
            "project_id": pid,
            "project_name": header.get("project_name", ""),
            "head_contractor": header.get("head_contractor", ""),
            "status": header.get("status", ""),
            "keywords": [k.lower() for k in keywords if k],
            "contact_emails": [e.lower() for e in emails if e],
            "contact_domains": sorted({
                e.split("@")[-1] for e in emails if "@" in e
            }),
            "brief": str(brief.relative_to(WORKSPACE)),
        })
    return registry


# --------------------------------------------------------------------------- #
# Microsoft Graph
# --------------------------------------------------------------------------- #
def get_graph_token(client_id, secret):
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            tok = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[AUTH ERROR] {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return None
    # Confirm the token actually carries Mail.Read (admin consent present).
    payload = tok["access_token"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    roles = json.loads(base64.urlsafe_b64decode(payload)).get("roles", [])
    if not any(r.startswith("Mail.Read") for r in roles):
        print(f"[AUTH WARN] token roles lack Mail.Read: {roles}", file=sys.stderr)
    return tok["access_token"]


def fetch_messages(token, since_iso, page_cap=400):
    """Fetch inbox messages received on or after since_iso, newest first."""
    params = urllib.parse.urlencode({
        "$filter": f"receivedDateTime ge {since_iso}",
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,webLink,conversationId,flag,categories",
        "$orderby": "receivedDateTime desc",
        "$top": "50",
    })
    url = f"{GRAPH_BASE}/users/{MAILBOX}/mailFolders/inbox/messages?{params}"
    out = []
    while url and len(out) < page_cap:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Prefer": 'outlook.body-content-type="text"',
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"[GRAPH ERROR] {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
            break
        out.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return out[:page_cap]


def addr(entry):
    return ((entry or {}).get("emailAddress") or {}).get("address", "").lower()


def all_recipients(msg):
    people = [addr(msg.get("from"))]
    for r in msg.get("toRecipients", []) + msg.get("ccRecipients", []):
        people.append(addr(r))
    return [p for p in people if p]


def fetch_sent_conversations(token, since_iso, page_cap=400):
    """Map conversationId -> latest sentDateTime for mail Nathan sent in the
    window. Used to tell whether an inbound thread has already been replied to."""
    params = urllib.parse.urlencode({
        "$filter": f"sentDateTime ge {since_iso}",
        "$select": "conversationId,sentDateTime",
        "$orderby": "sentDateTime desc",
        "$top": "50",
    })
    url = f"{GRAPH_BASE}/users/{MAILBOX}/mailFolders/sentitems/messages?{params}"
    latest = {}
    fetched = 0
    while url and fetched < page_cap:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"[GRAPH WARN] sent items fetch {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            break
        for m in data.get("value", []):
            cid = m.get("conversationId")
            sent = m.get("sentDateTime")
            if cid and sent and sent > latest.get(cid, ""):
                latest[cid] = sent
            fetched += 1
        url = data.get("@odata.nextLink")
    return latest


def is_replied(msg, sent_map):
    """True if Nathan sent something in this thread at or after this message
    arrived (i.e. he has already responded to it)."""
    cid = msg.get("conversationId")
    if not cid or cid not in sent_map:
        return False
    return sent_map[cid] >= (msg.get("receivedDateTime") or "")


def is_flagged_needs_reply(msg):
    """True if Nathan has flagged the message for follow-up or tagged it with a
    'Needs Reply' / 'To Be Discussed' Outlook category."""
    if (msg.get("flag") or {}).get("flagStatus") == "flagged":
        return True
    cats = [c.lower() for c in (msg.get("categories") or [])]
    return any(k in cats for k in ("needs reply", "to be discussed"))


def should_alert(r):
    """Alert only on what is still on Nathan: a message that needs a reply and
    that he has not replied to yet.

    'Needs a reply' = an Outlook 'Needs Reply' / 'To Be Discussed' category or a
    follow-up flag (r['flagged']), or an important event type (Variation / RFI /
    Site Issue at high confidence). Reply-state (r['replied'], a sent message in
    the thread) is a UNIVERSAL suppressor: the Outlook category is sticky and is
    not cleared when Nathan replies, so without this gate already-handled mail
    keeps alerting. Returns (alert: bool, reason: str)."""
    if r.get("replied"):
        return False, ""
    if r.get("flagged"):
        return True, "needs-reply"
    c = r.get("classification") or {}
    if (c.get("event_type") in ALERT_TYPES
            and (c.get("confidence") or 0) >= ALERT_CONFIDENCE):
        return True, "important"
    return False, ""


# --------------------------------------------------------------------------- #
# Routing (pre-filter)
# --------------------------------------------------------------------------- #
def route_message(msg, registry):
    """Score each project against the message. Returns list of matches.

    Routing signals, strongest first:
      - contact email exact match (sender or any recipient)        weight 5
      - contact domain match                                       weight 3
      - project-number token match in subject/body (word boundary) weight 3
      - multi-word keyword phrase hit                              weight 2
      - non-generic single keyword hit                             weight 1
    A project routes when its score >= 3.
    """
    subject = (msg.get("subject") or "")
    preview = (msg.get("bodyPreview") or "")
    haystack = f"{subject}\n{preview}".lower()
    people = set(all_recipients(msg))
    domains = {p.split("@")[-1] for p in people if "@" in p}

    matches = []
    for proj in registry:
        score = 0
        reasons = []
        for em in proj["contact_emails"]:
            if em in people:
                score += 5
                reasons.append(f"contact {em}")
        for dom in proj["contact_domains"]:
            if dom in domains:
                score += 3
                reasons.append(f"domain {dom}")
        pid = proj["project_id"]
        if re.search(rf"(?<!\d){re.escape(pid)}(?!\d)", haystack):
            score += 3
            reasons.append(f"job# {pid}")
        for kw in proj["keywords"]:
            if kw == pid:
                continue
            if " " in kw:
                if kw in haystack:
                    score += 2
                    reasons.append(f'phrase "{kw}"')
            elif kw not in GENERIC_WORDS:
                if re.search(rf"\b{re.escape(kw)}\b", haystack):
                    score += 1
                    reasons.append(f'kw "{kw}"')
        if score >= 3:
            matches.append({
                "project_id": pid,
                "project_name": proj["project_name"],
                "score": score,
                "reasons": reasons,
            })
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches


def is_suppressed(msg):
    """True if the sender or subject matches a suppression rule.

    Sender list drops marketing/SaaS-admin mail; subject list drops threads
    owned by another system (e.g. WF5 subcontractor reports).
    """
    sender = addr(msg.get("from"))
    if any(pat in sender for pat in SUPPRESS_SENDERS):
        return True
    subject = (msg.get("subject") or "").lower()
    return any(pat in subject for pat in SUPPRESS_SUBJECTS)


def apply_type_overrides(msg, classification):
    """Deterministic event-type corrections applied after Claude classifies.

    Veyor sends every site booking confirmation (material, ute, crane) through
    the same flow; Claude occasionally typed crane bookings as Schedule Change
    while siblings were Delivery. Force the whole Veyor booking stream to
    Delivery for consistency (see phase1-accuracy report).
    """
    if not classification:
        return
    sender = addr(msg.get("from"))
    subject = (msg.get("subject") or "").lower()
    if "veyordigital.com" in sender and "booking" in subject:
        classification["event_type"] = "Delivery"


# Automated senders whose repeated notifications should collapse to one event
# (e.g. Procore "overdue defect" reminders re-sent daily). Human threads are
# never collapsed - each message in a real thread is its own event.
AUTOMATED_SENDERS = (
    "noreply", "no-reply", "notifications@", "procoretech.com",
    "hammertech", "veyordigital.com", "donotreply", "automated",
)


def is_automated(sender):
    return any(pat in sender for pat in AUTOMATED_SENDERS)


def normalize_subject(subject):
    """Strip RE:/FW:/FWD: prefixes and whitespace for dedup comparison."""
    s = (subject or "").lower().strip()
    while True:
        m = re.match(r"^(re|fw|fwd)\s*:\s*", s)
        if not m:
            break
        s = s[m.end():]
    return re.sub(r"\s+", " ", s).strip()


def dedup_automated(rows):
    """Collapse repeated automated notifications (same sender + subject) to the
    latest one. Returns (kept_rows, collapsed_count). Human mail is untouched."""
    seen = {}
    order = []
    collapsed = 0
    for r in rows:
        if not r.get("classification"):
            order.append(r)
            continue
        sender = r["from"]
        if is_automated(sender):
            key = (sender, normalize_subject(r["subject"]))
            if key in seen:
                # keep the most recent by date; count the rest as collapsed
                collapsed += 1
                kept = seen[key]
                if r["date"] > kept["date"]:
                    kept["_collapsed"] = kept.get("_collapsed", 1) + 1
                    # swap: drop old kept from order, keep newer
                    idx = order.index(kept)
                    r["_collapsed"] = kept.get("_collapsed", 1)
                    order[idx] = r
                    seen[key] = r
                else:
                    kept["_collapsed"] = kept.get("_collapsed", 1) + 1
                continue
            seen[key] = r
            order.append(r)
        else:
            order.append(r)
    return order, collapsed


def is_corporate(msg):
    """Privacy gate: at least one party on a corporate (.com.au/.com) domain."""
    for p in all_recipients(msg):
        dom = p.split("@")[-1]
        if dom.endswith(".com.au") or dom.endswith(".com") or dom.endswith(".net.au"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Classification (Claude)
# --------------------------------------------------------------------------- #
CLASSIFIER_SYSTEM = (
    "You are an email classifier for a structural steel project manager at "
    "Dunsteel. Given one email, classify it as a single project event. "
    "Return STRICT JSON only, no prose, no code fences. Schema:\n"
    "{\n"
    '  "event_type": one of ' + json.dumps(EVENT_TYPES) + ",\n"
    '  "summary": "1 to 2 line plain-language summary",\n'
    '  "key_facts": ["short fact", ...],   // delivery dates, $ amounts, RFI/variation numbers, drawing refs\n'
    '  "action_owner_hint": "who needs to act next, or empty",\n'
    '  "suggested_due_date": "YYYY-MM-DD or empty",\n'
    '  "confidence": 0.0 to 1.0\n'
    "}\n"
    "Event type guidance: Delivery = material/transport/galv dates; Variation = "
    "scope change, variation number, pricing of extra work; RFI = a FORMAL "
    "request for information at design or shop-drawing stage - a technical or "
    "design clarification about drawings, specs, or design intent. IMPORTANT: a "
    "head contractor or builder simply asking Dunsteel to do, provide, action, "
    "or attend to something operational is NOT an RFI. Classify that as General, "
    "or Schedule Change if it is about programme/dates/sequencing, or Site Issue "
    "if it is about a site problem. Reserve RFI for genuine design-stage queries. "
    "Decision = a choice made or requested; Schedule Change = programme/date "
    "movement, works programmes, sequencing; Finish Approval = sign-off on "
    "completed work; Site Issue = defect, safety, access, NCR; Commercial = "
    "claims, invoices, payments; General = anything else. No long dashes."
)


def classify(msg, route):
    """Classify one routed email on the Max subscription. Returns dict or error dict."""
    subject = msg.get("subject") or "(no subject)"
    body = (msg.get("bodyPreview") or "")[:1500]
    sender = addr(msg.get("from"))
    proj_line = ", ".join(f'{m["project_id"]} {m["project_name"]}' for m in route)
    user = (
        f"Routed project(s): {proj_line}\n"
        f"From: {sender}\n"
        f"Date: {msg.get('receivedDateTime', '')[:10]}\n"
        f"Subject: {subject}\n"
        f"Body preview:\n{body}\n"
    )
    try:
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        # claude_max handles fence-stripping + JSON parsing of the reply.
        return claude_max.complete_json(user, system=CLASSIFIER_SYSTEM, model="sonnet")
    except Exception as e:  # noqa: BLE001 - report any failure into the row
        return {"event_type": "General", "summary": f"[classify error: {e}]",
                "key_facts": [], "action_owner_hint": "", "suggested_due_date": "",
                "confidence": 0.0, "_error": str(e)}


# --------------------------------------------------------------------------- #
# Dry-run report
# --------------------------------------------------------------------------- #
def write_report(rows, registry, since_iso, total_fetched, today, suppressed_count=0):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / f"dry-run-{today}.md"
    json_path = OUT_DIR / f"dry-run-{today}.json"

    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    routed = [r for r in rows if r["route"]]
    classified = [r for r in routed if r.get("classification")]
    by_type = {}
    for r in classified:
        et = r["classification"].get("event_type", "?")
        by_type[et] = by_type.get(et, 0) + 1

    lines = []
    lines.append(f"# Email Router - Dry Run {today}")
    lines.append("")
    lines.append(f"- Mailbox: `{MAILBOX}`")
    lines.append(f"- Window: since `{since_iso}` (last {ARGS.days} days)")
    lines.append(f"- Fetched from inbox: **{total_fetched}**")
    lines.append(f"- Suppressed (marketing/SaaS-admin): **{suppressed_count}**")
    lines.append(f"- Routed to a project: **{len(routed)}**")
    lines.append(f"- Classified by Claude: **{len(classified)}**")
    lines.append(f"- Projects in registry: {len(registry)} "
                 f"({sum(1 for p in registry if p['contact_emails'])} have contact emails)")
    lines.append("")
    if by_type:
        lines.append("**Event types:** " + ", ".join(f"{k} ({v})" for k, v in sorted(by_type.items())))
        lines.append("")
    lines.append("Score the two right-hand columns by hand: put Y/N for whether the "
                 "routed project and event type are correct. That is the Phase 1 "
                 "accuracy measurement (targets: project >= 85%, event type >= 75%).")
    lines.append("")
    lines.append("| Date | From | Subject | Routed (score) | Why | Event Type | Conf | Proj OK? | Type OK? |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in routed:
        m = r["route"][0]
        others = "" if len(r["route"]) == 1 else f" +{len(r['route'])-1}"
        why = "; ".join(m["reasons"][:3])
        c = r.get("classification") or {}
        subj = (r["subject"] or "").replace("|", "\\|")[:50]
        frm = r["from"][:28]
        lines.append(
            f"| {r['date']} | {frm} | {subj} | {m['project_id']}{others} ({m['score']}) "
            f"| {why} | {c.get('event_type','-')} | {c.get('confidence','-')} |  |  |"
        )
    lines.append("")

    # Unrouted corporate mail - candidates for keyword/contact backfill.
    unrouted = [r for r in rows if not r["route"] and r["corporate"]]
    if unrouted:
        lines.append(f"## Unrouted corporate mail ({len(unrouted)})")
        lines.append("")
        lines.append("These came from a corporate domain but matched no project. "
                     "If any belong to a project, add the sender to that brief's "
                     "`contact_emails` or add a keyword, then re-run.")
        lines.append("")
        lines.append("| Date | From | Subject |")
        lines.append("|---|---|---|")
        for r in unrouted[:60]:
            subj = (r["subject"] or "").replace("|", "\\|")[:60]
            lines.append(f"| {r['date']} | {r['from'][:32]} | {subj} |")
        lines.append("")

    # Detail blocks for routed + classified items.
    lines.append("## Classified events (detail)")
    lines.append("")
    for r in classified:
        c = r["classification"]
        m = r["route"][0]
        lines.append(f"### {r['date']} | {m['project_id']} {m['project_name']} | {c.get('event_type')}")
        lines.append(f"- From: {r['from']}")
        lines.append(f"- Subject: {r['subject']}")
        lines.append(f"- Summary: {c.get('summary','')}")
        if c.get("key_facts"):
            lines.append(f"- Key facts: {'; '.join(c['key_facts'])}")
        if c.get("action_owner_hint"):
            lines.append(f"- Action owner: {c['action_owner_hint']}")
        if c.get("suggested_due_date"):
            lines.append(f"- Suggested due: {c['suggested_due_date']}")
        lines.append(f"- Confidence: {c.get('confidence')}  |  Route score: {m['score']} ({'; '.join(m['reasons'])})")
        lines.append(f"- [Open in Outlook]({r['weblink']})")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


# --------------------------------------------------------------------------- #
# Live writers: Notion Project Events + Telegram alerts
# --------------------------------------------------------------------------- #
NOTION_DB_PROJECT_EVENTS = "YOUR_NOTION_ID"
NOTION_DB_PROJECTS = "YOUR_NOTION_ID_2"
NOTION_VERSION = "2022-06-28"


def _notion_headers():
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        return None
    return {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"}


def _notion_post(path, payload, headers):
    req = urllib.request.Request(f"https://api.notion.com/v1/{path}",
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def notion_project_page_map(headers):
    """number (str) -> Notion page id, for setting the Project relation."""
    out = {}
    payload = {"page_size": 100}
    try:
        data = _notion_post(f"databases/{NOTION_DB_PROJECTS}/query", payload, headers)
    except urllib.error.HTTPError as e:
        print(f"[NOTION] projects query failed {e.code}: {e.read().decode()[:200]}")
        return out
    for p in data.get("results", []):
        num = (p.get("properties", {}).get("Project Number", {}) or {}).get("number")
        if num is not None:
            out[str(int(num))] = p["id"]
    return out


def notion_existing_source_ids(headers):
    """Collect Source ID / URL plain-text values already in the events DB, for
    idempotency. Paginates fully."""
    seen = set()
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        try:
            data = _notion_post(f"databases/{NOTION_DB_PROJECT_EVENTS}/query", payload, headers)
        except urllib.error.HTTPError as e:
            print(f"[NOTION] events query failed {e.code}: {e.read().decode()[:200]}")
            break
        for p in data.get("results", []):
            rt = (p.get("properties", {}).get("Source ID / URL", {}) or {}).get("rich_text", [])
            for t in rt:
                txt = t.get("plain_text", "")
                if txt:
                    seen.add(txt)
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break
    return seen


def notion_create_event(row, project_pages, headers):
    """Create one Project Events page from a routed+classified row."""
    c = row["classification"]
    m = row["route"][0]
    conf = c.get("confidence")
    status = "Auto-ingested" if (conf or 0) >= 0.7 else "Auto-ingested - low confidence"
    # Title: [PID] event type - short subject
    title = f"[{m['project_id']}] {c.get('event_type','General')} - {row['subject'][:80]}"
    raw = (row.get("bodyhint") or "")
    collapsed = row.get("_collapsed")
    raw_note = f"(recurring: {collapsed} notifications collapsed)\n" if collapsed else ""
    facts = "; ".join(c.get("key_facts", []) or [])

    props = {
        "Title": {"title": [{"text": {"content": title[:200]}}]},
        "Event Type": {"select": {"name": c.get("event_type", "General")}},
        "Source": {"select": {"name": "Outlook"}},
        "Source ID / URL": {"rich_text": [{
            "text": {"content": row["msg_id"][:200] or "outlook",
                     "link": {"url": row["weblink"]} if row.get("weblink") else None}}]},
        "Date": {"date": {"start": row["date"]}},
        "Summary": {"rich_text": [{"text": {"content": (c.get("summary", "") or "")[:1800]}}]},
        "Confidence": {"number": conf if isinstance(conf, (int, float)) else None},
        "Status": {"select": {"name": status}},
        "Raw extract": {"rich_text": [{"text": {"content": (raw_note + f"From: {row['from']}\nKey facts: {facts}")[:1800]}}]},
    }
    due = c.get("suggested_due_date")
    if due and re.match(r"^\d{4}-\d{2}-\d{2}$", str(due)):
        props["Due Date"] = {"date": {"start": due}}
    page_id = project_pages.get(str(m["project_id"]))
    if page_id:
        props["Project"] = {"relation": [{"id": page_id}]}

    payload = {"parent": {"database_id": NOTION_DB_PROJECT_EVENTS}, "properties": props}
    return _notion_post("pages", payload, headers)


def _md_to_telegram_html(text):
    """Convert the light markdown Claude emits into Telegram-safe HTML.

    Telegram plain text shows literal asterisks, so **bold** never rendered.
    HTML parse mode only needs &, <, > escaped (far safer than MarkdownV2, which
    would need every . - ( ) ! escaped). Also puts a blank line before each
    bullet so talking points are not jammed together.
    """
    # 1) escape HTML specials first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2) **bold** then *bold* -> <b>bold</b> (double handled first)
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*\n]+?)\*", r"<b>\1</b>", text)
    # 3) blank line before each bullet line for readability
    text = re.sub(r"\n(?=[-•]\s)", "\n\n", text)
    # collapse any run of 3+ newlines back to a double
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def telegram_alert(text):
    """Send a Telegram alert if a bot token + chat id are configured. Returns
    True if sent, False if skipped (no creds) or failed. Renders light markdown
    as HTML; falls back to plain text if the formatted send is rejected so a
    message is never lost to a formatting error."""
    token = os.environ.get("AGENT_BOT_TOKEN") or os.environ.get("DUNSTEEL_BOT_TOKEN")
    chat = os.environ.get("AGENT_ALLOWED_CHAT_IDS") or os.environ.get("DUNSTEEL_CHAT_ID")
    if not token or not chat:
        return False
    chat = chat.split(",")[0].strip()
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    def _send(payload):
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(urllib.request.Request(
            endpoint, data=data, method="POST"), timeout=15)

    base = {"chat_id": chat, "disable_web_page_preview": "true"}
    try:
        _send({**base, "text": _md_to_telegram_html(text), "parse_mode": "HTML"})
        return True
    except Exception:  # noqa: BLE001 - formatted send rejected; retry as plain text
        try:
            _send({**base, "text": text})
            return True
        except Exception:  # noqa: BLE001
            return False


def run_live(rows, today, alerts_enabled=True):
    """Write routed+classified events to Notion (idempotent) and fire alerts."""
    headers = _notion_headers()
    if not headers:
        print("[LIVE] no NOTION_API_KEY; cannot write. Aborting live run.")
        return
    routed = [r for r in rows if r.get("route") and r.get("classification")]
    routed, collapsed = dedup_automated(routed)
    routed = [r for r in routed if r.get("route") and r.get("classification")]
    print(f"[LIVE] {len(routed)} events after collapsing {collapsed} repeated notifications.")

    project_pages = notion_project_page_map(headers)
    existing = notion_existing_source_ids(headers)
    print(f"[LIVE] {len(existing)} events already in Notion (idempotency check).")

    created = skipped = failed = alerts = 0
    for r in routed:
        if r["msg_id"] and r["msg_id"] in existing:
            skipped += 1
            continue
        try:
            notion_create_event(r, project_pages, headers)
            created += 1
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  [write fail] {e.code}: {e.read().decode()[:160]}")
            continue
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [write fail] {e}")
            continue
        c = r["classification"]
        ok, reason = should_alert(r)
        if alerts_enabled and ok:
            m = r["route"][0]
            tag = "needs reply" if reason == "needs-reply" else c.get("event_type")
            sent = telegram_alert(
                f"[{m['project_id']}] {tag} ({c.get('confidence')})\n"
                f"{r['subject'][:120]}\n{c.get('summary','')[:200]}")
            if sent:
                alerts += 1

    print(f"[LIVE] created {created}, skipped {skipped} (already present), failed {failed}.")
    if alerts:
        print(f"[LIVE] {alerts} Telegram alerts sent.")
    else:
        print("[LIVE] Telegram alerts: none sent (no bot token/chat id set yet, or none qualified).")


# --------------------------------------------------------------------------- #
# Live writer: Supabase project_events (the migration target for the event store)
# --------------------------------------------------------------------------- #
def supabase_event_row(r):
    """Map a routed+classified router row to a project_events column dict."""
    c = r["classification"]
    m = r["route"][0]
    conf = c.get("confidence")
    status = "auto-ingested" if (conf or 0) >= 0.7 else "auto-ingested-low-confidence"
    due = c.get("suggested_due_date")
    if not (due and re.match(r"^\d{4}-\d{2}-\d{2}$", str(due))):
        due = None
    summary = (c.get("summary", "") or "")
    collapsed = r.get("_collapsed")
    if collapsed:
        summary = f"(recurring: {collapsed} notifications collapsed) {summary}"
    return {
        "msg_id": r["msg_id"] or r.get("weblink") or "",
        "project_id": str(m["project_id"]),
        "event_type": c.get("event_type", "General"),
        "source": "Outlook",
        "event_date": r["date"] or None,
        "sender": r.get("from", ""),
        "subject": (r.get("subject") or "")[:500],
        "summary": summary[:4000],
        "key_facts": c.get("key_facts", []) or [],
        "action_owner": c.get("action_owner"),
        "due_date": due,
        "confidence": conf if isinstance(conf, (int, float)) else None,
        "status": status,
        "weblink": r.get("weblink", ""),
    }


def run_live_supabase(rows, today, alerts_enabled=True):
    """Write routed+classified events to the Supabase project_events table
    (idempotent on msg_id) and fire Telegram alerts on newly-seen events only."""
    import supabase_events  # same dir; on sys.path when run as a script

    routed = [r for r in rows if r.get("route") and r.get("classification")]
    routed, collapsed = dedup_automated(routed)
    routed = [r for r in routed if r.get("route") and r.get("classification")]
    print(f"[LIVE] {len(routed)} events after collapsing {collapsed} repeated notifications.")

    # Preflight: is the table there? Fetch existing ids for new-vs-seen alerting.
    try:
        existing = {x["msg_id"] for x in supabase_events.query(select="msg_id") if x.get("msg_id")}
    except Exception as e:  # noqa: BLE001
        print(f"[LIVE] Supabase not ready: {e}")
        print("[LIVE] Run scripts/supabase_schema.sql in the Supabase SQL editor first.")
        return
    print(f"[LIVE] {len(existing)} events already in Supabase (idempotency check).")

    new = [r for r in routed if r.get("msg_id") and r["msg_id"] not in existing]
    payload = [supabase_event_row(r) for r in new]
    try:
        inserted = supabase_events.insert_events(payload) if payload else 0
    except Exception as e:  # noqa: BLE001
        print(f"[LIVE] Supabase insert failed: {e}")
        return
    skipped = len(routed) - len(new)
    print(f"[LIVE] inserted {inserted}, skipped {skipped} (already present).")

    alerts = 0
    if alerts_enabled:
        for r in new:
            ok, reason = should_alert(r)
            if not ok:
                continue
            c = r["classification"]
            m = r["route"][0]
            tag = "needs reply" if reason == "needs-reply" else c.get("event_type")
            if telegram_alert(
                    f"[{m['project_id']}] {tag} ({c.get('confidence')})\n"
                    f"{r['subject'][:120]}\n{c.get('summary','')[:200]}"):
                alerts += 1
    if alerts:
        print(f"[LIVE] {alerts} Telegram alerts sent.")
    else:
        print("[LIVE] Telegram alerts: none sent (no bot token/chat id set yet, or none qualified).")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    global ARGS
    parser = argparse.ArgumentParser(description="Dunsteel multi-project email router")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument("--live", action="store_true", help="write events to the store + Telegram (default: dry-run)")
    parser.add_argument("--store", choices=["supabase", "notion", "both"], default="supabase",
                        help="where --live writes events (default: supabase)")
    parser.add_argument("--no-alerts", action="store_true", help="live write but send no Telegram alerts (use for the initial backfill)")
    parser.add_argument("--no-classify", action="store_true", help="routing only, skip Claude")
    parser.add_argument("--page-cap", type=int, default=400, help="max messages to fetch")
    ARGS = parser.parse_args()

    load_dotenv()
    today = datetime.date.today().isoformat()
    since = datetime.datetime.utcnow() - datetime.timedelta(days=ARGS.days)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    registry = load_registry()
    print(f"Loaded {len(registry)} projects from the registry.")

    client_id, secret = read_outlook_creds()
    if not client_id or not secret:
        sys.exit("Could not read Outlook credentials from .env")
    token = get_graph_token(client_id, secret)
    if not token:
        sys.exit("Failed to get Graph token.")

    print(f"Fetching inbox since {since_iso} ...")
    messages = fetch_messages(token, since_iso, page_cap=ARGS.page_cap)
    print(f"Fetched {len(messages)} messages.")

    # Reply-state: which threads has Nathan already responded to in this window.
    sent_map = fetch_sent_conversations(token, since_iso, page_cap=ARGS.page_cap)
    print(f"Found replies in {len(sent_map)} sent threads (reply-state for alerts).")

    classify_enabled = not ARGS.no_classify
    if classify_enabled:
        # Classification bills the Claude Max subscription via claude_max, never
        # metered API credits. The VPS cron needs CLAUDE_CODE_OAUTH_TOKEN in .env
        # (no interactive login there); a developer PC can fall back to its login.
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            print("[classify] Claude Max subscription (CLAUDE_CODE_OAUTH_TOKEN).")
        else:
            print("[classify] No CLAUDE_CODE_OAUTH_TOKEN in env; relying on an "
                  "interactive Claude login. Set it in .env for the VPS cron "
                  "(run `claude setup-token`).")

    # Pre-classify idempotency: on a live supabase run, load the msg_ids already
    # in the store and skip re-classifying them. Without this, a short-interval
    # cron would re-run Claude over every email in the --days window each tick
    # (cost scales with the window, not new mail). With it, only genuinely new
    # messages hit the classifier, so 15-min polling stays cheap.
    seen_ids = set()
    if ARGS.live and ARGS.store in ("supabase", "both"):
        try:
            import supabase_events
            seen_ids = {x["msg_id"] for x in supabase_events.query(select="msg_id")
                        if x.get("msg_id")}
            print(f"{len(seen_ids)} events already in store; skipping re-classification of those.")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] could not load existing ids ({e}); classifying all in window.")

    rows = []
    routed_count = 0
    suppressed_count = 0
    for msg in messages:
        # Suppression gate first: drop marketing/SaaS-admin before anything else.
        if is_suppressed(msg):
            suppressed_count += 1
            rows.append({
                "msg_id": msg.get("id", ""),
                "date": (msg.get("receivedDateTime") or "")[:10],
                "from": addr(msg.get("from")),
                "subject": msg.get("subject") or "",
                "weblink": msg.get("webLink", ""),
                "corporate": False, "suppressed": True,
                "route": [], "classification": None,
            })
            continue
        route = route_message(msg, registry)
        # Privacy gate: only classify corporate, project-routed mail.
        corporate = is_corporate(msg)
        classification = None
        if route and corporate and classify_enabled and msg.get("id") not in seen_ids:
            classification = classify(msg, route)
            apply_type_overrides(msg, classification)
            routed_count += 1
            print(f"  [{routed_count}] {msg.get('receivedDateTime','')[:10]} "
                  f"-> {route[0]['project_id']} "
                  f"({classification.get('event_type')}, conf {classification.get('confidence')})")
        rows.append({
            "msg_id": msg.get("id", ""),
            "date": (msg.get("receivedDateTime") or "")[:10],
            "from": addr(msg.get("from")),
            "subject": msg.get("subject") or "",
            "weblink": msg.get("webLink", ""),
            "corporate": corporate,
            "suppressed": False,
            "route": route if corporate else [],   # drop personal mail from routing
            "classification": classification,
            "replied": is_replied(msg, sent_map),
            "flagged": is_flagged_needs_reply(msg),
        })

    # Always write the report for the record (dry-run and live).
    md_path, json_path = write_report(rows, registry, since_iso, len(messages),
                                      today, suppressed_count)
    print(f"\nReport: {md_path}")
    print(f"Raw JSON: {json_path}")
    print(f"Suppressed (marketing/SaaS-admin): {suppressed_count}")
    print(f"Routed + classified: {routed_count} of {len(messages)} fetched.")

    if ARGS.live:
        alerts_enabled = not ARGS.no_alerts
        if ARGS.store in ("supabase", "both"):
            print("\n[LIVE] writing events to Supabase ...")
            run_live_supabase(rows, today, alerts_enabled=alerts_enabled)
        if ARGS.store in ("notion", "both"):
            print("\n[LIVE] writing events to Notion ...")
            # When writing to both, Supabase already fired the alerts.
            run_live(rows, today, alerts_enabled=alerts_enabled and ARGS.store == "notion")


if __name__ == "__main__":
    main()
