#!/usr/bin/env python3
"""
voice_notes.py - quick voice capture to the Notion General Notes table.

Flow: a Telegram voice memo -> Deepgram transcript -> a light Claude structuring
pass (project, priority, task-vs-info) -> a row in General Notes. Built as a
standalone helper so it works on the existing ANTHROPIC_API_KEY and does not
depend on the Max-token brain being set up.

Public API:
    transcribe(audio_bytes, mimetype) -> str
    structure_note(transcript, active_job) -> dict
    log_note(struct) -> (ok: bool, url: str|None, detail: str)
    handle_voice(audio_bytes, mimetype, active_job) -> (reply_text, url)

General Notes DB (resolved 2026-06-04): YOUR_GENERAL_NOTES_DB_ID
  Note   [title]      the note / task text
  #      [rich_text or select]  the project number (501 / 504 / 505 / 502)
  Notes  [rich_text]  extra detail / context
  Priority [select]   High / Medium / Low
  Status [status]     Incomplete (= task to do) / Constant (= info) / ...

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
# Default General Notes DB (Nathan's). Per-PM instances override via the
# registry's 'general_notes' key; resolved through _general_notes_db().
GENERAL_NOTES_DB = "YOUR_GENERAL_NOTES_DB_ID"
REGISTRY = WORKSPACE / "outputs" / "internal" / "site-diary-db-registry.json"
NOTION_VERSION = "2022-06-28"
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&punctuate=true&language=en&filler_words=true"
STRUCT_MODEL = "claude-sonnet-4-6"

# Projects voice notes can be directed to (Nathan's active set). A project named
# in the note overrides the chat's active job; otherwise the active job is used.
KNOWN_PROJECTS = {
    "501": ["501", "riverside", "stratus", "data centres", "syd2", "syd02", "northbridge"],
    "504": ["504", "meridian", "sy5-4", "syd5"],
    "505": ["505", "vantage", "vantage tech"],
    "502": ["502", "parkview", "parkline"],
}


def _load_dotenv():
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# --------------------------------------------------------------------------- #
# Transcription (Deepgram)
# --------------------------------------------------------------------------- #
def transcribe_full(audio_bytes, mimetype="audio/ogg"):
    """Transcribe and return {transcript, confidence, duration}. confidence and
    duration are None if Deepgram does not supply them. Use this where the Voice
    Memo Log columns (Audio Duration, Deepgram Confidence) should be populated."""
    _load_dotenv()
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        raise RuntimeError("no DEEPGRAM_API_KEY in .env")
    import sys
    print(f"[transcribe] audio_bytes: {len(audio_bytes)} bytes, mimetype: {mimetype}", file=sys.stderr)
    req = urllib.request.Request(
        DEEPGRAM_URL, data=audio_bytes, method="POST",
        headers={"Authorization": f"Token {key}", "Content-Type": mimetype})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = resp.read()
            print(f"[transcribe] deepgram response: {len(resp_data)} bytes", file=sys.stderr)
            data = json.loads(resp_data)
        alt = data["results"]["channels"][0]["alternatives"][0]
        transcript = (alt.get("transcript") or "").strip()
        confidence = alt.get("confidence")
        duration = data.get("metadata", {}).get("duration")
        print(f"[transcribe] result: {len(transcript)} chars, conf={confidence}, "
              f"dur={duration}, first 100: {transcript[:100]!r}", file=sys.stderr)
        if not transcript:
            print(f"[transcribe] WARNING: empty transcript from Deepgram. Full response: {data!r}", file=sys.stderr)
        return {"transcript": transcript, "confidence": confidence, "duration": duration}
    except Exception as e:
        print(f"[transcribe] ERROR: {e}, request_size: {len(audio_bytes)}", file=sys.stderr)
        raise


def transcribe(audio_bytes, mimetype="audio/ogg"):
    """Back-compat wrapper: returns just the transcript string."""
    return transcribe_full(audio_bytes, mimetype)["transcript"]


# --------------------------------------------------------------------------- #
# Structuring (Claude)
# --------------------------------------------------------------------------- #
def _detect_project(text, active_job):
    """Project named in the note wins over the active job."""
    low = text.lower()
    for pid, kws in KNOWN_PROJECTS.items():
        for kw in kws:
            if re.search(rf"\b{re.escape(kw)}\b", low):
                return pid
    return active_job or ""


def structure_note(transcript, active_job=""):
    """Turn a raw transcript into a tidy note. Falls back to a sensible default
    if no API key, so capture still works."""
    _load_dotenv()
    project = _detect_project(transcript, active_job)
    fallback = {
        "note": transcript[:200], "project": project, "detail": "",
        "priority": "Medium", "is_task": True,
    }
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import claude_max
        sys_prompt = (
            "You tidy a spoken quick-note from a steel project manager into JSON. "
            "Return STRICT JSON only:\n"
            '{"note": "short imperative title, <= 120 chars",'
            ' "project": "one of 501/504/505/502 or empty",'
            ' "detail": "any extra context, or empty",'
            ' "priority": "High|Medium|Low",'
            ' "is_task": true if it is something to do, false if it is just info}\n'
            f"Active project context: {active_job or 'none'}. If the note names a "
            "different project, use that. Keep the note wording close to what was "
            "said. No long dashes."
        )
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        out = claude_max.complete_json(transcript, system=sys_prompt, model="sonnet")
        if not out.get("project"):
            out["project"] = project           # keyword/active-job fallback
        return out
    except Exception:  # noqa: BLE001
        return fallback


# --------------------------------------------------------------------------- #
# Notion write (type-aware on the # field)
# --------------------------------------------------------------------------- #
def _headers():
    key = os.environ.get("NOTION_API_KEY")
    return {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"} if key else None


def _project_field_type(headers):
    """Read whether '#' is rich_text or select so we write the right shape."""
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{_general_notes_db()}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        db = json.loads(resp.read())
    return db.get("properties", {}).get("#", {}).get("type", "rich_text")


def log_note(struct):
    _load_dotenv()
    headers = _headers()
    if not headers:
        return False, None, "no NOTION_API_KEY"
    note = (struct.get("note") or "").strip() or "(empty note)"
    project = (struct.get("project") or "").strip()
    detail = (struct.get("detail") or "").strip()
    priority = struct.get("priority") or "Medium"
    status = "Incomplete" if struct.get("is_task", True) else "Constant"

    props = {
        "Note": {"title": [{"text": {"content": note[:200]}}]},
        "Priority": {"select": {"name": priority}},
        "Status": {"status": {"name": status}},
    }
    if detail:
        props["Notes"] = {"rich_text": [{"text": {"content": detail[:1800]}}]}
    if project:
        try:
            ftype = _project_field_type(headers)
        except Exception:  # noqa: BLE001
            ftype = "rich_text"
        if ftype == "select":
            props["#"] = {"select": {"name": project}}
        elif ftype == "multi_select":
            props["#"] = {"multi_select": [{"name": project}]}
        else:
            props["#"] = {"rich_text": [{"text": {"content": project}}]}

    payload = {"parent": {"database_id": _general_notes_db()}, "properties": props}
    try:
        req = urllib.request.Request("https://api.notion.com/v1/pages",
                                     data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        return True, page.get("url"), "ok"
    except urllib.error.HTTPError as e:
        return False, None, f"{e.code}: {e.read().decode()[:200]}"


def _load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {}


def _general_notes_db():
    """The General Notes DB for this instance. Per-PM via the registry's
    'general_notes' key (or the NOTION_GENERAL_NOTES_DB env override); falls
    back to the module default so Nathan's behaviour is unchanged."""
    reg = _load_registry()
    return (reg.get("general_notes")
            or os.environ.get("NOTION_GENERAL_NOTES_DB")
            or GENERAL_NOTES_DB)


def _vml_props_for_schema(properties, transcript, captured_by, duration_secs,
                          confidence, status, today, now):
    """Build a Notion properties payload that matches whatever columns a given
    Voice Memo Log actually has. The four logs do not share one schema (501 uses
    'Name'/'Date'/'Confidence'; the others use 'Date'(title)/'Timestamp'/
    'Deepgram Confidence'), so we map by the live property name + type instead of
    hard-coding column names. Anything the DB does not have is simply skipped."""
    props = {}
    for name, meta in properties.items():
        t = meta.get("type")
        low = name.lower()
        if t == "title":
            # Title is the row label: the date reads cleanly in every log.
            props[name] = {"title": [{"text": {"content": today}}]}
        elif t == "rich_text" and "transcri" in low:
            props[name] = {"rich_text": [{"text": {"content": transcript[:1900]}}]}
        elif t == "date":
            # 'Timestamp'/'... time' get the full instant; a plain 'Date' gets the day.
            start = now if ("stamp" in low or "time" in low) else today
            props[name] = {"date": {"start": start}}
        elif t == "select" and "captured" in low:
            props[name] = {"select": {"name": captured_by}}
        elif t == "select" and "status" in low:
            props[name] = {"select": {"name": status}}
        elif t == "status" and "status" in low:
            props[name] = {"status": {"name": status}}
        elif t == "number" and "duration" in low and duration_secs is not None:
            props[name] = {"number": round(float(duration_secs), 1)}
        elif t == "number" and "confidence" in low and confidence is not None:
            props[name] = {"number": round(float(confidence), 4)}
    return props


def write_to_voice_memo_log(transcript, project, captured_by="Nathan",
                             duration_secs=None, confidence=None, status="Buffered"):
    """Write a transcription to the project's Voice Memo Log DB immediately.
    Returns (ok, page_id, detail). Schema-aware: reads the target DB's live
    properties and only writes columns that exist, so it works across the
    differing log schemas and surfaces a real error string on failure (the
    caller should display it rather than swallow it)."""
    _load_dotenv()
    headers = _headers()
    if not headers:
        return False, None, "no NOTION_API_KEY"

    reg = _load_registry()
    db_id = reg.get("voice_memo_logs", {}).get(str(project))
    if not db_id:
        return False, None, f"no voice_memo_log DB registered for project {project}"

    # Read the live schema so we write the right shape for this specific log.
    try:
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{db_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            schema = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:160]
        return False, None, f"DB {project} unreachable ({e.code}): {body}"
    except Exception as e:  # noqa: BLE001
        return False, None, f"DB {project} schema read failed: {e}"

    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    props = _vml_props_for_schema(
        schema.get("properties", {}), transcript, captured_by,
        duration_secs, confidence, status, today, now)

    payload = {"parent": {"database_id": db_id}, "properties": props}
    try:
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        return True, page.get("id"), "ok"
    except urllib.error.HTTPError as e:
        return False, None, f"{e.code}: {e.read().decode()[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, None, str(e)


def handle_voice(audio_bytes, mimetype, active_job=""):
    """End to end: bytes -> transcript -> structured note -> Notion. Returns a
    short confirmation string for the chat, plus the Notion url."""
    transcript = transcribe(audio_bytes, mimetype)
    if not transcript:
        return "Could not hear anything in that voice note - try again?", None
    struct = structure_note(transcript, active_job)
    ok, url, detail = log_note(struct)
    kind = "task" if struct.get("is_task", True) else "note"
    proj = struct.get("project") or "no project"
    if ok:
        return (f"Logged ({proj}, {kind}, {struct.get('priority','Medium')}):\n"
                f"\"{struct.get('note')}\""), url
    return f"Heard: \"{transcript[:120]}\"\nbut the Notion write failed: {detail}", None


if __name__ == "__main__":
    # Offline test of the structuring + Notion write (no audio needed).
    _load_dotenv()
    demo = structure_note("501, chase Sam on the Shell C variation, pretty urgent", "")
    print("structured:", demo)
    print("(not writing in test mode; call log_note(demo) to write)")
