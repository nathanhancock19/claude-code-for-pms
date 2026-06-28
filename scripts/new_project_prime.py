#!/usr/bin/env python3
"""
new_project_prime.py - the worker behind the /new-project skill.

Primes Nathan on a project whose S: drive folders already exist (set up by
admin). It runs a full pipeline:

  1. S: drive lookup    - find the project folder by number prefix under
                          "S:\\Operations\\01 Current Project" (falling back to
                          "02 Completed Projects"). Parse the folder name into
                          [number] - [head contractor] - [project name].
  2. S: drive inventory - shallow, read-only walk of the project folder. Captures
                          01 Tender Information, 02 Contractural (Contract +
                          Claims), 09 Costing-Quotes, the schedule folder
                          (13 Schedules or 13 Program), 04 Variations, and a
                          top-level listing. No writes, moves, or deletes ever
                          touch the S: drive.
  3. Outlook scrape     - search Nathan's mailbox via Microsoft Graph for emails
                          related to the project over the last 6 months. Graph
                          auth is DETECTED first: if the credential is not
                          available (no TENANT_ID, or token request fails), the
                          step is skipped cleanly and a placeholder summary is
                          written. The script never hangs waiting for auth.
  4. Notion page        - create a page in the Projects DB via the Notion MCP.
                          This worker does NOT call the MCP itself (the skill
                          layer does). The worker emits a NOTION PLAN describing
                          exactly what would be created (title, properties,
                          parent DB) so the skill can either create it for real
                          or print it in --dry-run mode.
  5. Workspace files    - create / update reference/projects/[number]-[slug]/
                          with project-brief.md (full YAML + extracted data),
                          s-drive-index.md, outlook-thread-summary.md, and
                          contacts.md. Idempotent: an existing brief is enriched
                          in place, never duplicated.
  6. Context update      - add or update the project's row in
                          context/dunsteel-projects.md.

Usage:
    python scripts/new_project_prime.py 432
    python scripts/new_project_prime.py 432 --dry-run
    python scripts/new_project_prime.py 509 --dry-run --name "Hartwell Group - 60 KS"

Flags:
    --dry-run     Do not create a live Notion page and do not mutate
                  context/dunsteel-projects.md. Report exactly what WOULD be
                  created / changed. The local reference files ARE still written
                  (they are the safe, idempotent deliverable). Use this for the
                  first supervised run.
    --name STR    Override the parsed "head contractor - project name" if the
                  folder name is ambiguous or does not split cleanly.
    --months N    Outlook look-back window in months (default 6).
    --no-outlook  Skip the Outlook step entirely (do not even attempt auth).

Environment (loaded from the workspace-root .env, zero dependency):
    ANTHROPIC_API_KEY   Reserved for future brief synthesis. Not required.
    Microsoft Graph     CLIENT_ID + SECRET + TENANT_ID. The working Graph OAuth
                        currently lives inside n8n and is NOT reusable from a
                        standalone script, so TENANT_ID is typically absent and
                        the Outlook step is skipped gracefully.

HARD RULE: no long dashes anywhere in this file or in any generated content.
Use a hyphen, a colon, or restructure the sentence.
"""

import argparse
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
REFERENCE_PROJECTS = WORKSPACE / "reference" / "projects"
PROJECTS_CONTEXT = WORKSPACE / "context" / "dunsteel-projects.md"

S_CURRENT = Path(r"S:\Operations\01 Current Project")
S_COMPLETED = Path(r"S:\Operations\02 Completed Projects")

MAILBOX = "nathanh@dunsteel.com.au"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Notion Projects DB. The skill layer resolves the live data source id via the
# Notion MCP (notion-search for "Projects"); this is the documented target so
# the dry-run output is concrete.
NOTION_PROJECTS_DB = "Dunsteel Projects Hub > Projects Database"

# Subfolders the inventory cares about. The schedule folder name varies between
# projects ("13 Schedules" on some, "13 Program" on others), so both are tried.
SUBFOLDERS = {
    "tender": "01 Tender Information",
    "contractual": "02 Contractural",
    "costing": "09 Costing-Quotes",
    "variations": "04 Variations",
    "scopes": "05 Scopes",
    "methodology": "07 Installation Methodologys",
}
SCHEDULE_FOLDER_CANDIDATES = ["13 Schedules", "13 Program", "13 Programme"]


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_dotenv():
    """Populate os.environ from the workspace-root .env (zero dependency).

    Does not overwrite a value already present in the real environment. The .env
    in this workspace contains some non-standard lines (e.g. a key with a space
    in its name, and multi-line JSON); we parse defensively and only take simple
    KEY=VALUE lines.
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
            # Skip obviously composite keys (e.g. "NOTION_API_KEY (Dunsteel...)").
            if not re.match(r"^[A-Z0-9_.]+$", key):
                continue
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "project"


def parse_folder_name(folder_name: str) -> dict:
    """Split '[number] - [head contractor] - [project name]' into parts."""
    parts = [p.strip() for p in folder_name.split(" - ")]
    out = {"number": parts[0] if parts else "", "builder": "", "project_name": ""}
    if len(parts) >= 3:
        out["builder"] = parts[1]
        out["project_name"] = " - ".join(parts[2:])
    elif len(parts) == 2:
        out["project_name"] = parts[1]
    return out


def find_project_folder(number: str):
    """Return (folder Path, location_label) for '[number] -*', or (None, reason)."""
    for root, label in [(S_CURRENT, "01 Current Project"),
                        (S_COMPLETED, "02 Completed Projects")]:
        if not root.exists():
            continue
        matches = [p for p in root.iterdir()
                   if p.is_dir() and re.match(rf"^{re.escape(number)}\s*-", p.name)]
        if len(matches) == 1:
            return matches[0], label
        if len(matches) > 1:
            # Ambiguous - return the list so the caller can ask Nathan.
            names = "; ".join(m.name for m in matches)
            return None, f"multiple folders match '{number} -*' in {label}: {names}"
    return None, f"no folder matching '{number} -*' under {S_CURRENT} or {S_COMPLETED}"


def existing_slug_dir(number: str):
    """Find an existing reference/projects/[number]-* dir for idempotency."""
    if not REFERENCE_PROJECTS.exists():
        return None
    for p in REFERENCE_PROJECTS.iterdir():
        if p.is_dir() and re.match(rf"^{re.escape(number)}-", p.name):
            return p
    return None


def parse_brief_yaml(brief_path: Path) -> dict:
    """Extract the existing YAML front matter from a brief, if present.

    Lightweight parser (no PyYAML): handles scalar keys and simple list keys.
    """
    data = {}
    if not brief_path.exists():
        return data
    text = brief_path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return data
    block = m.group(1)
    current_list = None
    for line in block.splitlines():
        if re.match(r"^\s*-\s+", line) and current_list is not None:
            item = line.strip()[1:].strip().strip('"').strip("'")
            data[current_list].append(item)
            continue
        m2 = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m2:
            continue
        key, val = m2.group(1), m2.group(2).strip()
        if val == "" or val == "[]":
            # "" opens a block list on following lines; "[]" is an empty inline list.
            data[key] = []
            current_list = key if val == "" else None
        else:
            data[key] = val.strip('"').strip("'")
            current_list = None
    return data


# ---------------------------------------------------------------------------
# S: drive inventory (shallow, read-only)
# ---------------------------------------------------------------------------

def list_dir(path: Path, limit: int = 60):
    """Return (dirs, files) name lists for a directory, read-only. Empty if missing."""
    dirs, files = [], []
    if not path.exists() or not path.is_dir():
        return dirs, files
    try:
        for entry in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            name = entry.name
            # Skip Office lock files and OS cruft.
            if name.startswith("~$") or name in ("Thumbs.db",):
                continue
            if entry.is_dir():
                dirs.append(name)
            else:
                files.append(name)
    except OSError:
        pass
    return dirs[:limit], files[:limit]


def inventory_project(folder: Path) -> dict:
    """Build a structured, shallow inventory of the project folder."""
    inv = {"top_dirs": [], "top_files": [], "sections": {}, "schedule_folder": None}
    inv["top_dirs"], inv["top_files"] = list_dir(folder)

    for key, sub in SUBFOLDERS.items():
        p = folder / sub
        d, f = list_dir(p)
        # Go one level deeper for contractual (Contract + Claims live in subfolders).
        nested = {}
        if key == "contractual":
            for child in ("Contract", "Claims"):
                cd, cf = list_dir(p / child)
                if cd or cf:
                    nested[child] = {"dirs": cd, "files": cf}
        inv["sections"][key] = {
            "folder": sub,
            "exists": p.exists(),
            "dirs": d,
            "files": f,
            "nested": nested,
        }

    # Schedule folder: try each candidate name.
    for cand in SCHEDULE_FOLDER_CANDIDATES:
        p = folder / cand
        if p.exists():
            d, f = list_dir(p)
            # one level deeper, schedule subfolders often hold the live program
            nested = {}
            for sub in d[:6]:
                cd, cf = list_dir(p / sub)
                if cf or cd:
                    nested[sub] = {"dirs": cd, "files": cf}
            inv["schedule_folder"] = {"folder": cand, "dirs": d, "files": f, "nested": nested}
            break

    return inv


# ---------------------------------------------------------------------------
# Outlook scrape via Microsoft Graph (auth-detected)
# ---------------------------------------------------------------------------

def graph_auth_available():
    """Return (token, status_message). token is None if auth is unavailable."""
    client_id = os.environ.get("CLIENT_ID", "").strip()
    client_secret = os.environ.get("SECRET", "").strip()
    tenant_id = (os.environ.get("TENANT_ID", "")
                 or os.environ.get("MICROSOFT_TENANT_ID", "")).strip()

    if not client_id or not client_secret:
        return None, "Graph CLIENT_ID / SECRET not found in .env"
    if not tenant_id:
        return None, ("Graph TENANT_ID not set in .env. The working Graph OAuth lives "
                      "inside n8n and is not reusable from a standalone script. "
                      "Add TENANT_ID (and an app with Mail.Read application permission "
                      "plus admin consent) to enable the Outlook scrape.")

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            tok = json.loads(resp.read()).get("access_token")
            if tok:
                return tok, "Graph token acquired"
            return None, "Graph token response had no access_token"
    except urllib.error.HTTPError as e:
        return None, f"Graph token request failed ({e.code})"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"Graph token request error: {e}"


def graph_search(token: str, query: str, months: int, top: int = 15):
    """Search the mailbox for a query string over the last N months."""
    since = (datetime.date.today() - datetime.timedelta(days=30 * months)).isoformat()
    params = urllib.parse.urlencode({
        "$search": f'"{query}"',
        "$select": "subject,from,toRecipients,receivedDateTime,bodyPreview,webLink",
        "$top": str(top),
    })
    url = f"{GRAPH_BASE}/users/{MAILBOX}/messages?{params}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "ConsistencyLevel": "eventual",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            msgs = json.loads(resp.read()).get("value", [])
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return []
    out = []
    for m in msgs:
        if (m.get("receivedDateTime", "") or "")[:10] < since:
            continue
        sender = m.get("from", {}).get("emailAddress", {})
        out.append({
            "subject": m.get("subject", ""),
            "from_name": sender.get("name", ""),
            "from_addr": sender.get("address", ""),
            "date": (m.get("receivedDateTime", "") or "")[:10],
            "preview": (m.get("bodyPreview", "") or "")[:200],
            "link": m.get("webLink", ""),
        })
    return out


def run_outlook_scrape(ctx: dict, months: int, no_outlook: bool):
    """Return (results_by_query dict or None, status_message)."""
    if no_outlook:
        return None, "Outlook step skipped (--no-outlook)"
    load_dotenv()
    token, status = graph_auth_available()
    if not token:
        return None, status
    queries = [q for q in {ctx["number"], ctx["project_name"], ctx["builder"]} if q]
    results = {}
    for q in queries:
        results[q] = graph_search(token, q, months)
    return results, f"Outlook scrape complete via Graph (look-back {months} months)"


# ---------------------------------------------------------------------------
# File writers (idempotent)
# ---------------------------------------------------------------------------

def yaml_list(items):
    if not items:
        return " []"
    return "\n" + "\n".join(f'  - "{i}"' for i in items)


def write_brief(ref_dir: Path, ctx: dict, inv: dict, existing_yaml: dict, today: str):
    """Write or enrich project-brief.md. Idempotent: preserves existing
    YAML values (contact_emails, end_client, contract_type, status) where the
    enrichment has nothing better, so a re-run never wipes hand-added detail."""
    brief_path = ref_dir / "project-brief.md"
    is_new = not brief_path.exists()

    # Merge YAML: prefer existing non-empty scalar values; union list values.
    end_client = existing_yaml.get("end_client") or "TBC (confirm from contract / tender)"
    status = existing_yaml.get("status") or "active"
    contract_type = existing_yaml.get("contract_type") or "TBC (confirm from 02 Contractural)"
    contract_value = existing_yaml.get("contract_value") or ""
    contact_emails = existing_yaml.get("contact_emails") or []
    if isinstance(contact_emails, str):
        contact_emails = [contact_emails] if contact_emails else []
    # Drop any stray empty-list token that an earlier pass may have written.
    contact_emails = [e for e in contact_emails if e and e not in ("[]", "")]

    # Keyword set: number, builder words, project name words, plus existing.
    kw = set()
    for v in existing_yaml.get("keywords", []) if isinstance(existing_yaml.get("keywords"), list) else []:
        kw.add(v)
    kw.add(ctx["number"])
    for w in re.split(r"[\s\-]+", f"{ctx['builder']} {ctx['project_name']}".lower()):
        if len(w) > 2:
            kw.add(w)
    keywords = sorted(kw)

    yaml = (
        "---\n"
        f"project_id: {ctx['number']}\n"
        f'project_name: {ctx["project_name"]}\n'
        f'head_contractor: {ctx["builder"]}\n'
        f"end_client: {end_client}\n"
        f"status: {status}\n"
        f"contract_type: {contract_type}\n"
        + (f"contract_value: {contract_value}\n" if contract_value else "")
        + "keywords:" + yaml_list(keywords) + "\n"
        + "contact_emails:" + yaml_list(contact_emails) + "\n"
        f"sdrive_path: {ctx['sdrive_path']}\n"
        f"last_enriched: {today}\n"
        "---\n"
    )

    sec = inv["sections"]

    def fmt_files(section_key, label):
        s = sec.get(section_key, {})
        lines = [f"### {label}", ""]
        if not s.get("exists"):
            lines.append(f"- Folder `{s.get('folder','')}` not present on S: drive.")
            return "\n".join(lines)
        any_content = False
        nested = s.get("nested") or {}
        for f in s.get("files", []):
            lines.append(f"- {f}")
            any_content = True
        for d in s.get("dirs", []):
            # If we have an expanded listing for this child, skip the bare line.
            if d in nested:
                continue
            lines.append(f"- {d}/")
            any_content = True
        for child, cc in nested.items():
            lines.append(f"- {child}/")
            for f in cc.get("files", []):
                lines.append(f"  - {f}")
            for d in cc.get("dirs", []):
                lines.append(f"  - {d}/")
            any_content = True
        if not any_content:
            lines.append("- (empty or no readable entries)")
        return "\n".join(lines)

    sched = inv.get("schedule_folder")
    sched_lines = ["### Programme / Schedule", ""]
    if sched:
        sched_nested = sched.get("nested") or {}
        sched_lines.append(f"Folder on S: drive: `{sched['folder']}`")
        sched_lines.append("")
        for f in sched.get("files", []):
            sched_lines.append(f"- {f}")
        for d in sched.get("dirs", []):
            sched_lines.append(f"- {d}/")
            for f in (sched_nested.get(d) or {}).get("files", []):
                sched_lines.append(f"  - {f}")
    else:
        sched_lines.append("No `13 Schedules` / `13 Program` folder found, or it is empty.")
    sched_block = "\n".join(sched_lines)

    body = f"""
# Project Brief: {ctx['number']} - {ctx['project_name']}

**Head Contractor:** {ctx['builder'] or 'TBC'}
**Client / End User:** {end_client}
**Status:** {status}
**Contract Type:** {contract_type}
**Dunsteel Scope Summary:** {existing_yaml.get('_scope_summary') or 'Structural steel supply and installation. Confirm full scope from the 01 Tender Information and 05 Scopes folders (see S: drive index below).'}
**S: Drive:** `{ctx['sdrive_path']}`
**Last Enriched:** {today} (via /new-project)

> This brief was generated / enriched by `/new-project`. The S: drive index and
> contacts are auto-extracted from a shallow read-only folder scan. Commercial
> figures, scope detail, and contacts should be confirmed against the source
> documents before relying on them for a claim or variation.

## Scope Detail

To confirm from these sources (see full index in `s-drive-index.md`):

{fmt_files('tender', '01 Tender Information')}

{fmt_files('scopes', '05 Scopes')}

{fmt_files('methodology', '07 Installation Methodologys')}

## Contract

{fmt_files('contractual', '02 Contractural')}

## Commercial

{fmt_files('costing', '09 Costing-Quotes')}

{fmt_files('variations', '04 Variations')}

## Programme

{sched_block}

## Key Contacts

See `contacts.md`. Contacts are extracted from the Outlook scrape when Graph
auth is available; otherwise this is seeded from the head contractor and any
emails already in the YAML front matter.

## Outstanding Items / Risks

- [ ] Confirm contract type and value from `02 Contractural/Contract`.
- [ ] Confirm current scope (early works vs main works) from tender + scope folders.
- [ ] Review the variation register for open / pending variations.
- [ ] Run the Outlook scrape once Graph credentials are available (see `outlook-thread-summary.md`).

## Reference Files Index

Full S: drive index: `s-drive-index.md`.
Local reference folder: `reference/projects/{ref_dir.name}/`

---

*Brief enriched {today} by /new-project from a shallow read-only S: drive scan.*
"""

    brief_path.write_text(yaml + body, encoding="utf-8")
    return brief_path, ("created" if is_new else "updated")


def write_sdrive_index(ref_dir: Path, ctx: dict, inv: dict, today: str):
    path = ref_dir / "s-drive-index.md"
    lines = [
        f"# S: Drive Index: {ctx['number']} - {ctx['project_name']}",
        "",
        f"**Root:** `{ctx['sdrive_path']}`",
        f"**Generated:** {today} (shallow read-only scan by /new-project)",
        "",
        "## Top-level folders",
        "",
    ]
    for d in inv["top_dirs"]:
        lines.append(f"- {d}/")
    for f in inv["top_files"]:
        lines.append(f"- {f}")
    lines.append("")

    def block(title, section_key):
        s = inv["sections"].get(section_key, {})
        lines.append(f"## {title}  (`{s.get('folder','')}`)")
        lines.append("")
        if not s.get("exists"):
            lines.append("- (folder not present)")
            lines.append("")
            return
        nested = s.get("nested") or {}
        for f in s.get("files", []):
            lines.append(f"- {f}")
        for d in s.get("dirs", []):
            if d in nested:
                continue
            lines.append(f"- {d}/")
        for child, cc in nested.items():
            lines.append(f"- {child}/")
            for f in cc.get("files", []):
                lines.append(f"  - {f}")
            for d in cc.get("dirs", []):
                lines.append(f"  - {d}/")
        lines.append("")

    block("Tender Information", "tender")
    block("Contractual", "contractual")
    block("Costing and Quotes", "costing")
    block("Variations", "variations")
    block("Scopes", "scopes")
    block("Installation Methodologies", "methodology")

    sched = inv.get("schedule_folder")
    lines.append("## Programme / Schedule")
    lines.append("")
    if sched:
        sched_nested = sched.get("nested") or {}
        lines.append(f"Folder: `{sched['folder']}`")
        lines.append("")
        for f in sched.get("files", []):
            lines.append(f"- {f}")
        for d in sched.get("dirs", []):
            lines.append(f"- {d}/")
            for f in (sched_nested.get(d) or {}).get("files", []):
                lines.append(f"  - {f}")
    else:
        lines.append("- (no 13 Schedules / 13 Program folder found)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Listing is shallow and read-only. Nothing on the S: drive was written, moved, or deleted.*")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_contacts(ref_dir: Path, ctx: dict, outlook, existing_yaml: dict, today: str):
    path = ref_dir / "contacts.md"
    lines = [
        f"# Contacts: {ctx['number']} - {ctx['project_name']}",
        "",
        f"**Generated:** {today} by /new-project",
        "",
        "| Name | Role | Organisation | Contact |",
        "|---|---|---|---|",
        f"| (head contractor team) | - | {ctx['builder'] or 'TBC'} | extract from Outlook scrape |",
        "| Nathan Hancock | Project Coordinator | Dunsteel | nathanh@dunsteel.com.au, 04XX XXX XXX |",
        "",
    ]

    # Seed from any emails already in YAML.
    seeded = existing_yaml.get("contact_emails") or []
    if isinstance(seeded, str):
        seeded = [seeded] if seeded else []
    if seeded:
        lines.append("## Email addresses on file (from existing brief)")
        lines.append("")
        for e in seeded:
            lines.append(f"- {e}")
        lines.append("")

    if outlook:
        # Build a de-duplicated sender list from the scrape.
        senders = {}
        for q, msgs in outlook.items():
            for m in msgs:
                addr = m.get("from_addr", "")
                if addr and addr.lower() != MAILBOX.lower():
                    senders.setdefault(addr, m.get("from_name", ""))
        if senders:
            lines.append("## Contacts extracted from Outlook (last scrape)")
            lines.append("")
            lines.append("| Name | Email |")
            lines.append("|---|---|")
            for addr, name in sorted(senders.items()):
                lines.append(f"| {name or '-'} | {addr} |")
            lines.append("")
    else:
        lines.append("## Outlook contacts")
        lines.append("")
        lines.append("Outlook scrape did not run (see `outlook-thread-summary.md`). "
                     "Once Graph auth is available, re-run /new-project to populate "
                     "the contact list from email correspondence.")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_outlook_summary(ref_dir: Path, ctx: dict, outlook, status: str, months: int, today: str):
    path = ref_dir / "outlook-thread-summary.md"
    lines = [
        f"# Outlook Thread Summary: {ctx['number']} - {ctx['project_name']}",
        "",
        f"**Generated:** {today} by /new-project",
        f"**Look-back window:** {months} months",
        f"**Status:** {status}",
        "",
    ]
    if not outlook:
        lines += [
            "## Outlook scrape pending Graph credential",
            "",
            "The Outlook scrape did not run on this pass. Reason:",
            "",
            f"> {status}",
            "",
            "The working Microsoft Graph OAuth currently lives inside n8n (WF1 email "
            "monitor) and is not reusable from a standalone script. To enable this step:",
            "",
            "1. Add `TENANT_ID` to the workspace `.env` (Azure AD > Overview > Tenant ID).",
            "2. Ensure the app (CLIENT_ID in .env) has Microsoft Graph **Mail.Read** "
            "application permission with admin consent.",
            "3. Re-run `python scripts/new_project_prime.py "
            f"{ctx['number']}` (without --dry-run once you are happy with the rest).",
            "",
            "Until then, contacts and correspondence in this brief are seeded only from "
            "the S: drive and any emails already recorded in the brief YAML.",
            "",
        ]
    else:
        for q, msgs in outlook.items():
            lines.append(f"## Search: \"{q}\"  ({len(msgs)} hits in window)")
            lines.append("")
            if not msgs:
                lines.append("- (no matching email in the look-back window)")
                lines.append("")
                continue
            for m in msgs:
                lines.append(f"- **{m['date']}** | {m['from_name']} <{m['from_addr']}> | {m['subject']}")
                if m.get("preview"):
                    lines.append(f"  - {m['preview']}")
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Notion plan (dry-run aware)
# ---------------------------------------------------------------------------

def build_notion_plan(ctx: dict, existing_yaml: dict) -> dict:
    """Describe exactly what the skill would create in Notion. The worker does
    NOT call the Notion MCP; the skill layer does, using this plan."""
    return {
        "action": "create_page",
        "parent_database": NOTION_PROJECTS_DB,
        "title": f"{ctx['number']} - {ctx['project_name']}",
        "properties": {
            "Project Name": ctx["project_name"],
            "Project Number": ctx["number"],
            "Status": existing_yaml.get("status", "Active") or "Active",
            "Head Contractor": ctx["builder"],
            "PM": "Nathan Hancock",
        },
        "note": ("Sub-pages (Site Diary, Defects, Voice Memo Log, Variations) follow "
                 "the existing 501 project-page pattern and should be created from the "
                 "template project page rather than from scratch."),
    }


# ---------------------------------------------------------------------------
# context/dunsteel-projects.md update
# ---------------------------------------------------------------------------

def context_row_plan(ctx: dict, ref_dir_name: str) -> dict:
    """Return the row that would be added / updated in the active projects table."""
    brief_rel = f"reference/projects/{ref_dir_name}/project-brief.md"
    return {
        "number": ctx["number"],
        "name": f"{ctx['project_name']} ({ctx['builder']})" if ctx["builder"] else ctx["project_name"],
        "brief": brief_rel,
        "row": (f"| {ctx['number']} | {ctx['project_name']} | Active | "
                f"Brief: `{brief_rel}` |"),
    }


def update_context_table(ctx: dict, ref_dir_name: str, dry_run: bool) -> str:
    """Idempotently update the project's row in the active projects table.

    Returns a status string. In dry-run, reports without writing.
    """
    plan = context_row_plan(ctx, ref_dir_name)
    if dry_run:
        return f"WOULD update context row for {ctx['number']}:\n    {plan['row']}"
    if not PROJECTS_CONTEXT.exists():
        return f"context file not found: {PROJECTS_CONTEXT}"

    text = PROJECTS_CONTEXT.read_text(encoding="utf-8")
    lines = text.splitlines()
    num = ctx["number"]
    # Match a table row whose first cell is the number (allowing **bold** markers).
    row_re = re.compile(rf"^\|\s*\*{{0,2}}{re.escape(num)}\*{{0,2}}\s*\|")
    replaced = False
    for i, line in enumerate(lines):
        if row_re.match(line):
            lines[i] = plan["row"]
            replaced = True
            break
    if not replaced:
        # Insert after the last data row of the first markdown table.
        header_idx = next((i for i, l in enumerate(lines)
                           if l.strip().startswith("| #") or l.strip().startswith("| # |")), None)
        if header_idx is not None:
            j = header_idx + 2  # skip header + separator
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            lines.insert(j, plan["row"])
            replaced = True
    PROJECTS_CONTEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (f"context row {'updated' if replaced else 'appended'} for {num}: {plan['row']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Prime a new Dunsteel project (S: drive + Outlook + Notion + workspace).")
    parser.add_argument("project", help="Project number, e.g. 432")
    parser.add_argument("--dry-run", action="store_true",
                        help="No live Notion page, no context/ mutation; report what would happen.")
    parser.add_argument("--name", default=None,
                        help='Override "head contractor - project name" if the folder name is ambiguous.')
    parser.add_argument("--months", type=int, default=6, help="Outlook look-back in months (default 6).")
    parser.add_argument("--no-outlook", action="store_true", help="Skip the Outlook step entirely.")
    args = parser.parse_args()

    number = args.project.strip()
    today = datetime.date.today().isoformat()
    load_dotenv()

    print(f"=== /new-project | {number} | {'DRY RUN' if args.dry_run else 'LIVE'} ===\n")

    # 1. Locate the S: drive folder.
    folder, loc = find_project_folder(number)
    if folder is None:
        print(f"[STOP] {loc}")
        print("       Provide the exact folder via --name, or confirm the project number.")
        sys.exit(2)
    print(f"[1/6] S: drive folder: {folder}\n      ({loc})")

    parsed = parse_folder_name(folder.name)
    builder = parsed["builder"]
    project_name = parsed["project_name"]

    # Read existing brief YAML early so we can preserve a hand-curated project
    # name / builder rather than regressing to the raw folder split.
    existing_dir = existing_slug_dir(number)
    pre_brief = (existing_dir / "project-brief.md") if existing_dir else None
    pre_yaml = parse_brief_yaml(pre_brief) if pre_brief else {}
    if pre_yaml.get("project_name"):
        project_name = pre_yaml["project_name"]
    if pre_yaml.get("head_contractor"):
        builder = pre_yaml["head_contractor"]

    if args.name:
        # --name "Builder - Project Name" (explicit override wins over everything)
        np = [p.strip() for p in args.name.split(" - ", 1)]
        if len(np) == 2:
            builder, project_name = np
        else:
            project_name = args.name.strip()
    ctx = {
        "number": number,
        "builder": builder,
        "project_name": project_name or folder.name,
        "sdrive_path": str(folder),
    }
    print(f"      Parsed: builder='{ctx['builder']}'  project='{ctx['project_name']}'")

    # 2. Inventory.
    inv = inventory_project(folder)
    n_sections = sum(1 for s in inv["sections"].values() if s["exists"])
    print(f"[2/6] Inventory: {len(inv['top_dirs'])} top folders, "
          f"{n_sections}/{len(SUBFOLDERS)} target sections present, "
          f"schedule folder: {inv['schedule_folder']['folder'] if inv['schedule_folder'] else 'none'}")

    # Reference dir (idempotent slug). existing_dir + pre_yaml resolved above.
    if existing_dir is not None:
        ref_dir = existing_dir
    else:
        ref_dir = REFERENCE_PROJECTS / f"{number}-{slugify(ctx['project_name'])}"
    ref_dir.mkdir(parents=True, exist_ok=True)
    brief_path = ref_dir / "project-brief.md"
    existing_yaml = pre_yaml or parse_brief_yaml(brief_path)
    print(f"      Reference dir: {ref_dir}  ({'existing - enriching' if existing_dir else 'new'})")

    # 3. Outlook scrape (auth-detected).
    outlook, outlook_status = run_outlook_scrape(ctx, args.months, args.no_outlook)
    print(f"[3/6] Outlook: {outlook_status}")

    # 4. Notion plan (worker emits plan; skill layer creates it unless --dry-run).
    notion_plan = build_notion_plan(ctx, existing_yaml)
    if args.dry_run:
        print("[4/6] Notion (DRY RUN - no page created). WOULD create:")
    else:
        print("[4/6] Notion plan (skill layer creates this via the Notion MCP):")
    print(f"      Parent DB : {notion_plan['parent_database']}")
    print(f"      Title     : {notion_plan['title']}")
    for k, v in notion_plan["properties"].items():
        print(f"      - {k}: {v}")
    print(f"      Note      : {notion_plan['note']}")

    # 5. Workspace files (always written - safe + idempotent).
    bpath, action = write_brief(ref_dir, ctx, inv, existing_yaml, today)
    sidx = write_sdrive_index(ref_dir, ctx, inv, today)
    osum = write_outlook_summary(ref_dir, ctx, outlook, outlook_status, args.months, today)
    cont = write_contacts(ref_dir, ctx, outlook, existing_yaml, today)
    print(f"[5/6] Workspace files:")
    print(f"      brief  ({action}): {bpath}")
    print(f"      index          : {sidx}")
    print(f"      outlook        : {osum}")
    print(f"      contacts       : {cont}")

    # 6. context/dunsteel-projects.md.
    ctx_status = update_context_table(ctx, ref_dir.name, args.dry_run)
    print(f"[6/6] Context table: {ctx_status}")

    # Machine-readable plan for the skill layer (so it can drive the Notion MCP).
    plan_out = {
        "project": ctx,
        "reference_dir": str(ref_dir),
        "files": {
            "brief": str(bpath),
            "s_drive_index": str(sidx),
            "outlook_summary": str(osum),
            "contacts": str(cont),
        },
        "outlook": {"ran": outlook is not None, "status": outlook_status},
        "notion_plan": notion_plan,
        "context_update": ctx_status,
        "dry_run": args.dry_run,
    }
    plan_path = ref_dir / "_new_project_plan.json"
    plan_path.write_text(json.dumps(plan_out, indent=2), encoding="utf-8")

    print("\nDONE.")
    print(f"  Machine plan: {plan_path}")
    if args.dry_run:
        print("  DRY RUN: no Notion page created, context/ not mutated. "
              "Local reference files were written (safe + idempotent).")
    if outlook is None:
        print("  KNOWN GAP: Outlook scrape pending Graph credential (see outlook-thread-summary.md).")


if __name__ == "__main__":
    main()
