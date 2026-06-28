#!/usr/bin/env python3
"""
diary.py - site diary capture + end-of-day build for the Telegram brain.

Model (Nathan's design): during the day he sends voice notes narrating who is
on which job doing what, often several projects in one note. Each transcript is
buffered. At end of day (auto cron, or /diary) the whole day's buffer is analysed
together, split by project, and written as one Subcontractors Diary entry per
project. Tasks mentioned in passing are pulled out to General Notes.

  append_note(transcript)          buffer one transcript for today
  buffer_count(date)               how many notes captured today
  run_eod(date) -> summary         analyse + write + return a Telegram summary

Diary targets (outputs/internal/site-diary-db-registry.json): one Dunsteel
Subcontractors Diary per project. Status type differs per DB (501 = status,
others = select) so the writer detects it.

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date as date_cls, datetime
from pathlib import Path

# The PM works in Sydney; the VPS clock is UTC. Buffer files and the end-of-day
# build must use the Sydney date, or notes sent before ~10am AEST get filed under
# the previous (often already-built) day. zoneinfo is stdlib on the VPS (py3.14).
try:
    from zoneinfo import ZoneInfo
    _SYD = ZoneInfo("Australia/Sydney")
except Exception:  # noqa: BLE001
    _SYD = None

WS = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = Path(__file__).resolve().parent
BUFFER_DIR = AGENT_DIR / "diary_buffer"
REGISTRY = WS / "outputs" / "internal" / "site-diary-db-registry.json"
GENERAL_NOTES_DB = "YOUR_GENERAL_NOTES_DB_ID"
NV = "2022-06-28"
MODEL = "claude-sonnet-4-6"
PROJECTS = ["501", "504", "505", "502"]


def _load_dotenv():
    env = WS / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        l = raw.strip()
        if l and not l.startswith("#") and "=" in l:
            k, _, v = l.partition("="); k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def _today():
    """Sydney's current date (the PM's working day), not the VPS UTC date."""
    if _SYD is not None:
        return datetime.now(_SYD).date().isoformat()
    return date_cls.today().isoformat()


def _registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {}


# --------------------------------------------------------------------------- #
# Buffer
# --------------------------------------------------------------------------- #
def append_note(transcript, job="", when=None, voice_memo_page_id=None):
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    day = (when or _today())
    entry = {"text": transcript, "job": job}
    if voice_memo_page_id:
        entry["voice_memo_page_id"] = voice_memo_page_id
    line = json.dumps(entry, ensure_ascii=False)
    with (BUFFER_DIR / f"{day}.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_buffer(day=None):
    """Returns a list of {text, job, voice_memo_page_id?} dicts."""
    day = day or _today()
    f = BUFFER_DIR / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            entry = {"text": d.get("text", ""), "job": d.get("job", "")}
            if d.get("voice_memo_page_id"):
                entry["voice_memo_page_id"] = d["voice_memo_page_id"]
            out.append(entry)
        except Exception:  # noqa: BLE001
            out.append({"text": line, "job": ""})
    return out


def buffer_count(day=None):
    return len(read_buffer(day))


# --------------------------------------------------------------------------- #
# Notion helpers
# --------------------------------------------------------------------------- #
def _H():
    return {"Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
            "Notion-Version": NV, "Content-Type": "application/json"}


def _get_db(db_id):
    req = urllib.request.Request(f"https://api.notion.com/v1/databases/{db_id}", headers=_H())
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _create_page(payload):
    req = urllib.request.Request("https://api.notion.com/v1/pages",
                                 data=json.dumps(payload).encode(), headers=_H(), method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# --------------------------------------------------------------------------- #
# Analysis (Claude)
# --------------------------------------------------------------------------- #
ANALYSIS_SYSTEM = (
    "You build a structural-steel project manager's end-of-day site diary from "
    "his raw voice notes for the day. The notes are casual and often cover "
    "several projects in one breath. Projects: 501 (Stratus Data Centres Riverside "
    "/ Northbridge), 504 (Meridian Data Centres / head contractor), 505 (Vantage "
    "Tech / head contractor), 502 (Parkview / Parkline Builders). Produce ONE entry "
    "per project that had activity.\n\n"
    "The diary entry must be THOROUGH and FAITHFUL. Capture everything "
    "substantive that was actually said for that project, do not compress it to "
    "a one-line summary, and do NOT invent or pad anything that was not said. If "
    "a detail was not mentioned, leave that field out rather than guess.\n\n"
    "In activity_notes, write a full, specific narrative (as long as needed) that "
    "includes, whenever the notes mention them: who was on site and how many, "
    "what each crew/person did, plant and equipment used (e.g. crane tonnage, "
    "EWP, man box), any delays and roughly how long they cost, any issue or "
    "defect or clash discovered, anything that needs coordination or a decision "
    "(e.g. RFI, contract scope question, design query), what is planned next / "
    "tomorrow, weather if noted, and any builder or head-contractor instruction. "
    "Write it as flowing diary prose, not bullet fragments.\n\n"
    "Return STRICT JSON only:\n"
    "{\n"
    '  "entries": [\n'
    '    {"project": "501|504|505|502",\n'
    '     "title": "short label summarising the day for this project, <= 120 chars",\n'
    '     "who_was_onsite": ["first names, or external crews as company x count e.g. a rigging crew x3"],\n'
    '     "activity_notes": "full faithful narrative as described above",\n'
    '     "scope": ["area/level/stair touched, if mentioned, else omit"],\n'
    '     "finish_time": "e.g. Full day, or a time, if mentioned, else omit",\n'
    '     "hours_lost": 0,\n'
    '     "safety_incident": false, "builder_delay": false}\n'
    "  ],\n"
    '  "tasks": [\n'
    '    {"note": "short imperative task he mentioned to do",\n'
    '     "project": "501|504|505|502 or empty", "priority": "High|Medium|Low"}\n'
    "  ]\n"
    "}\n"
    "Set hours_lost to a number only if a delay duration was stated, else 0. Set "
    "builder_delay true only if the head contractor or builder caused a delay or "
    "hold. Set safety_incident true only if a safety incident or near miss was "
    "described. Each note is prefixed with [job:NNN], the project that was active "
    "when it was sent: use it ONLY when the note text does not itself name a "
    "project. Only include tasks that are genuinely something to do, not diary "
    "observations. No long dashes anywhere."
)


def analyze_day(notes):
    """notes: list of {text, job} dicts (or plain strings). The job hint is the
    project active when the note was sent, used only when the text does not name
    a project itself."""
    _load_dotenv()
    rows = []
    for n in notes:
        if isinstance(n, dict):
            hint = n.get("job") or "?"
            rows.append(f"- [job:{hint}] {n.get('text','')}")
        else:
            rows.append(f"- [job:?] {n}")
    joined = "\n".join(rows)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import claude_max
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        return claude_max.complete_json(
            f"Today's voice notes:\n{joined}", system=ANALYSIS_SYSTEM, model="sonnet")
    except Exception as e:  # noqa: BLE001
        return {"entries": [], "tasks": [], "_error": f"claude_max: {e}"}


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #
def _rich_text_chunks(text, limit=1900):
    """Notion caps a single rich_text item at 2000 chars. A full diary narrative
    can run longer, so split it into <=limit chunks within the one property so
    nothing gets truncated."""
    text = text or ""
    if len(text) <= limit:
        return [{"text": {"content": text}}]
    return [{"text": {"content": text[i:i + limit]}}
            for i in range(0, len(text), limit)]


def _status_prop(db_schema, value):
    """Status is 'status' type on 501, 'select' on the new ones."""
    t = db_schema.get("properties", {}).get("Status", {}).get("type")
    if t == "status":
        return {"status": {"name": value}}
    if t == "select":
        return {"select": {"name": value}}
    return None


def _mark_memos_compiled(page_ids):
    """Patch Voice Memo Log entries from Buffered -> Compiled after site diary is built."""
    if not page_ids:
        return
    headers = _H()
    for page_id in page_ids:
        payload = {"properties": {"Status": {"select": {"name": "Compiled"}}}}
        try:
            req = urllib.request.Request(
                f"https://api.notion.com/v1/pages/{page_id}",
                data=json.dumps(payload).encode(), headers=headers, method="PATCH")
            urllib.request.urlopen(req, timeout=20).read()
        except Exception:  # noqa: BLE001
            pass  # non-fatal; diary entry is already written


def write_entries(analysis, day=None):
    _load_dotenv()
    day = day or _today()
    reg = _registry()
    written = []
    voice_memo_page_ids = [
        n["voice_memo_page_id"] for n in read_buffer(day)
        if n.get("voice_memo_page_id")
    ]
    for e in analysis.get("entries", []):
        pid = str(e.get("project", "")).strip()
        db_id = reg.get(pid)
        if not db_id:
            written.append(f"{pid}: no diary DB")
            continue
        try:
            schema = _get_db(db_id)
        except Exception as ex:  # noqa: BLE001
            written.append(f"{pid}: schema read fail {ex}")
            continue
        sprops = schema.get("properties", {})
        # Title: a descriptive day label (old diary style), falling back to the date.
        title = (e.get("title") or "").strip() or day
        props = {
            "Entry Date": {"title": [{"text": {"content": title[:120]}}]},
        }
        if "Entry Date (Date)" in sprops:
            props["Entry Date (Date)"] = {"date": {"start": day}}
        who = [w for w in (e.get("who_was_onsite") or []) if w][:25]
        if who:
            props["Who Was Onsite"] = {"multi_select": [{"name": w[:90]} for w in who]}
        if e.get("activity_notes"):
            props["Activity Notes"] = {"rich_text": _rich_text_chunks(e["activity_notes"])}
        scope = [s for s in (e.get("scope") or []) if s][:10]
        if scope and "Scope" in sprops:
            props["Scope"] = {"multi_select": [{"name": s[:90]} for s in scope]}
        ft = (e.get("finish_time") or "").strip()
        if ft and "Finish Time" in sprops:
            props["Finish Time"] = {"rich_text": [{"text": {"content": ft[:200]}}]}
        if "Hours Lost" in sprops:
            try:
                hl = float(e.get("hours_lost") or 0)
            except (TypeError, ValueError):
                hl = 0
            if hl:
                props["Hours Lost"] = {"number": round(hl, 2)}
        if "Safety Incidents" in sprops:
            props["Safety Incidents"] = {"checkbox": bool(e.get("safety_incident"))}
        if "Builder Delays" in sprops:
            props["Builder Delays"] = {"checkbox": bool(e.get("builder_delay"))}
        sp = _status_prop(schema, "Not Invoiced")
        if sp:
            props["Status"] = sp
        try:
            page = _create_page({"parent": {"database_id": db_id}, "properties": props})
            written.append(f"{pid}: {', '.join(who) or 'entry'} -> {page.get('url','ok')}")
        except urllib.error.HTTPError as ex:
            written.append(f"{pid}: write fail {ex.code} {ex.read().decode()[:120]}")
    # Tasks -> General Notes (reuse voice_notes.log_note shape).
    task_lines = []
    for t in analysis.get("tasks", []):
        try:
            import voice_notes
            ok, url, _ = voice_notes.log_note({
                "note": t.get("note", ""), "project": t.get("project", ""),
                "detail": "", "priority": t.get("priority", "Medium"), "is_task": True})
            task_lines.append(f"- {t.get('note','')[:60]}" + (" ok" if ok else " (write failed)"))
        except Exception as ex:  # noqa: BLE001
            task_lines.append(f"- {t.get('note','')[:60]} ({ex})")
    # Mark Voice Memo Log entries compiled now that the diary is written.
    _mark_memos_compiled(voice_memo_page_ids)
    return written, task_lines


def run_eod(day=None, force=False):
    day = day or _today()
    marker = BUFFER_DIR / f"{day}.built"
    if marker.exists() and not force:
        return (f"Site diary for {day} is already built. "
                "Send /diary to rebuild it (adds fresh entries).")
    transcripts = read_buffer(day)
    if not transcripts:
        return f"No diary notes captured for {day}. Send some voice notes and I will build the diary."
    analysis = analyze_day(transcripts)
    if analysis.get("_error"):
        return f"Could not analyse today's notes: {analysis['_error']}"
    written, tasks = write_entries(analysis, day)
    lines = [f"End-of-day diary for {day} ({len(transcripts)} notes):", ""]
    lines += [f"Diary: {w}" for w in written]
    if tasks:
        lines += ["", "Tasks to General Notes:"] + tasks
    lines += ["", "Review / edit the entries in Notion (Status starts as Not Invoiced)."]
    try:
        (BUFFER_DIR / f"{day}.built").write_text("done", encoding="utf-8")
    except OSError:
        pass
    return "\n".join(lines)


if __name__ == "__main__":
    # Offline test: analyse a sample multi-project note, do not write.
    _load_dotenv()
    sample = ["Shane, Leo and Andreas on site at Riverside working on Stair 2. "
              "We've got three guys from a rigging crew working on the Zone B "
              "canopies at Parkview today. Remind me to order more chemset for 501."]
    import sys
    print(json.dumps(analyze_day(sample), indent=2))
