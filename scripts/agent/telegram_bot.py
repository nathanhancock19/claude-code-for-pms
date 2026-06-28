#!/usr/bin/env python3
"""
telegram_bot.py - Telegram interface to the Dunsteel operations brain.

Two-way chat: Nathan messages the bot from his phone, each message is handled by
agent_core.ask() (Claude Code on his Max plan, Opus, full tools + MCP), and the
reply comes back in the chat. Conversation context is kept per chat by reusing
the Claude session id across messages.

Security: the bot only answers chat ids on the allowlist. Everyone else is
ignored. This is a single-user brain that acts as Nathan, so the allowlist is
the hard boundary.

Commands:
    /job 501 set the active project. With a job set, voice notes and typed text
             both go straight to General Notes for that project. /job none clears
             it and returns text to the chat brain.
    /diary   switch the active job into site-diary capture: voice + text get
             buffered and built into the Subcontractors Diary at end of day (cron).
    /notes   switch back to General Notes capture (the default).
    /new     start a fresh conversation (drop the session memory)
    /whoami  show auth mode + your chat id
    (text with no active job) -> sent to the brain

    Skill commands (run a VPS workspace command via the brain; see
    reference/systems/agent-brain/vps-command-catalogue.md):
    /swms /ra /toolbox /audit /email /summarise /ask /research
    These route to the brain regardless of any active job, and keep routing to
    the brain (skill mode) until the brain signals it is done, so multi-turn
    qualifying-question flows work. Generated PDFs are sent back as documents via
    the brain's [[SEND_FILE: <path>]] marker.

Env:
    AGENT_BOT_TOKEN        Telegram bot token from @BotFather. Falls back to
                           DUNSTEEL_BOT_TOKEN if AGENT_BOT_TOKEN is unset.
    AGENT_ALLOWED_CHAT_IDS Comma list of allowed Telegram chat ids. Falls back to
                           DUNSTEEL_CHAT_ID. REQUIRED - the bot refuses to start
                           with an empty allowlist.

Run:
    pip install -r scripts/agent/requirements.txt
    python scripts/agent/telegram_bot.py

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import os
import re
import sys
from datetime import date as _date_cls, datetime
from pathlib import Path

# Match diary.py: the diary "day" is the Sydney date, not the VPS UTC date.
try:
    from zoneinfo import ZoneInfo
    _SYD = ZoneInfo("Australia/Sydney")
except Exception:  # noqa: BLE001
    _SYD = None

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_core  # noqa: E402
import voice_notes  # noqa: E402
import diary  # noqa: E402

STATE_FILE = Path(__file__).resolve().parent / ".bot_state.json"

# The PM whose name stamps captured notes/diary entries. Per-PM via env;
# defaults to Nathan so his instance is unchanged.
PM_NAME = os.environ.get("AGENT_PM_NAME", "Nathan")


def _voice_projects():
    """Projects this instance accepts voice capture for. AGENT_VOICE_PROJECTS
    (comma-separated) overrides; otherwise derive from the site-diary registry
    keys (per-PM); finally fall back to Nathan's set."""
    env = os.environ.get("AGENT_VOICE_PROJECTS", "").strip()
    if env:
        return {p.strip() for p in env.split(",") if p.strip()}
    reg = voice_notes._load_registry()
    keys = {k for k in reg if k not in ("_note", "voice_memo_logs", "general_notes")}
    return keys or {"501", "504", "505", "502"}


VOICE_PROJECTS = _voice_projects()


def _today():
    """Sydney's current date, so diary mode rolls over at Sydney midnight."""
    if _SYD is not None:
        return datetime.now(_SYD).date().isoformat()
    return _date_cls.today().isoformat()


def _load_state():
    """State shape: {"jobs": {chat_id: "501"}, "diary": {chat_id: "YYYY-MM-DD"}}.
    The diary value is the day diary-mode was switched on; it auto-expires the
    next day, so every morning starts back in plain notes mode."""
    if STATE_FILE.exists():
        try:
            import json
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"jobs": {}, "diary": {}}
        # Migrate the old flat {chat_id: "501"} format.
        if "jobs" not in raw and "diary" not in raw:
            return {"jobs": {k: v for k, v in raw.items() if isinstance(v, str)}, "diary": {}}
        raw.setdefault("jobs", {})
        raw.setdefault("diary", {})
        return raw
    return {"jobs": {}, "diary": {}}


def _save_state(state):
    import json
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load_dotenv():
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Per-chat Claude session id, so each chat keeps its own running context.
SESSIONS: dict[int, str] = {}

# Per-chat active project ("job") + diary-mode flag. Persisted across restarts.
STATE: dict = _load_state()

# Chats currently inside a skill flow (e.g. a SWMS the brain is still building /
# asking about). While active, plain text routes to the brain regardless of any
# job, so multi-turn qualifying-question flows are not swallowed by capture.
# In-memory only: a restart mid-skill just drops back to normal routing.
SKILL_ACTIVE: set[int] = set()

# The brain marks generated files and completion in its reply text. The bot turns
# [[SEND_FILE: <path>]] into a Telegram document and clears skill mode on
# [[SKILL_DONE]]. Both markers are stripped from the visible reply.
FILE_MARKER = re.compile(r"\[\[SEND_FILE:\s*(.+?)\]\]")
DONE_MARKER = "[[SKILL_DONE]]"


def _extract_markers(text: str):
    """Return (clean_text, [file paths], done_flag) from a brain reply."""
    paths = [m.strip() for m in FILE_MARKER.findall(text or "")]
    clean = FILE_MARKER.sub("", text or "")
    done = DONE_MARKER in clean
    clean = clean.replace(DONE_MARKER, "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, paths, done


async def _send_files(update, paths):
    """Send each marked file as a Telegram document. Only files inside the
    workspace are sent (the brain runs there); missing/outside paths are noted."""
    for raw in paths:
        try:
            rp = Path(raw).resolve()
            rp.relative_to(WORKSPACE)
        except (ValueError, OSError):
            await update.message.reply_text(f"(skipped a file outside the workspace: {raw})")
            continue
        if not rp.exists():
            await update.message.reply_text(f"(file not found: {rp.name})")
            continue
        try:
            with open(rp, "rb") as fh:
                await update.message.reply_document(document=fh, filename=rp.name)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"(could not send {rp.name}: {e})")


async def _deliver(update, chat_id, res) -> bool:
    """Send a brain result to the chat: text (chunked) + any marked files.
    Returns True if the brain signalled the skill is done."""
    if res.session_id:
        SESSIONS[chat_id] = res.session_id
    clean, paths, done = _extract_markers(res.text or "")
    reply = clean or "(no reply)"
    for i in range(0, len(reply), 3800):  # Telegram hard-caps at 4096 chars
        await update.message.reply_text(reply[i:i + 3800])
    await _send_files(update, paths)
    return done


def get_job(chat_id) -> str:
    return STATE["jobs"].get(str(chat_id), "")


def set_job(chat_id, num):
    STATE["jobs"][str(chat_id)] = num
    STATE["diary"].pop(str(chat_id), None)   # a fresh job starts in notes mode
    _save_state(STATE)


def clear_job(chat_id):
    STATE["jobs"].pop(str(chat_id), None)
    STATE["diary"].pop(str(chat_id), None)
    _save_state(STATE)


def diary_mode(chat_id) -> bool:
    """True only while diary mode is on AND it was switched on today."""
    return STATE["diary"].get(str(chat_id)) == _today()


def set_diary_mode(chat_id, on: bool):
    cid = str(chat_id)
    if on:
        STATE["diary"][cid] = _today()
    else:
        STATE["diary"].pop(cid, None)
    _save_state(STATE)


def allowed_ids() -> set[int]:
    raw = os.environ.get("AGENT_ALLOWED_CHAT_IDS") or os.environ.get("DUNSTEEL_CHAT_ID", "")
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def main():
    _load_dotenv()
    try:
        from telegram import Update, BotCommand
        from telegram.ext import (Application, CommandHandler, MessageHandler,
                                  filters, ContextTypes)
    except ImportError:
        sys.exit("python-telegram-bot not installed. Run: "
                 "pip install -r scripts/agent/requirements.txt")

    token = os.environ.get("AGENT_BOT_TOKEN") or os.environ.get("DUNSTEEL_BOT_TOKEN")
    if not token:
        sys.exit("No bot token. Set AGENT_BOT_TOKEN (or DUNSTEEL_BOT_TOKEN) in .env")

    allow = allowed_ids()
    if not allow:
        sys.exit("Empty allowlist. Set AGENT_ALLOWED_CHAT_IDS (or DUNSTEEL_CHAT_ID) "
                 "to your Telegram chat id. The bot will not start wide open.")

    print(f"Auth mode: {agent_core.auth_mode()}")
    print(f"Allowlist: {sorted(allow)}")

    def is_allowed(update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.id in allow)

    async def on_new(update, context):
        if not is_allowed(update):
            return
        SESSIONS.pop(update.effective_chat.id, None)
        SKILL_ACTIVE.discard(update.effective_chat.id)
        await update.message.reply_text("Fresh conversation. What do you need?")

    async def on_whoami(update, context):
        cid = update.effective_chat.id if update.effective_chat else "?"
        if not is_allowed(update):
            await update.message.reply_text(f"Not authorised. Your chat id is {cid}.")
            return
        await update.message.reply_text(f"Auth: {agent_core.auth_mode()}\nYour chat id: {cid}")

    async def on_job(update, context):
        if not is_allowed(update):
            return
        chat_id = update.effective_chat.id
        SKILL_ACTIVE.discard(chat_id)
        arg = " ".join(context.args).strip() if context.args else ""
        if not arg:
            cur = get_job(chat_id)
            if cur:
                mode = "diary" if diary_mode(chat_id) else "notes"
                await update.message.reply_text(f"Active job: {cur} ({mode} mode).")
            else:
                await update.message.reply_text(
                    "No active job. Set one with /job 501 (501/504/505/502), or /job none.\n"
                    "With a job set, voice and text get captured to Notion. "
                    "/job none returns to the chat brain.")
            return
        if arg.lower() in ("none", "clear", "off"):
            clear_job(chat_id)
            await update.message.reply_text(
                "Active job cleared. Back to the chat brain - text me and I will answer. "
                "Voice notes still log to General Notes.")
            return
        num = arg.split()[0].strip()
        if num not in VOICE_PROJECTS:
            await update.message.reply_text(f"'{num}' is not one of {sorted(VOICE_PROJECTS)}. Try /job 501.")
            return
        set_job(chat_id, num)
        await update.message.reply_text(
            f"Active job set to {num}. Voice and text now go straight to General Notes for {num}.\n"
            "/diary to switch to site-diary capture, /job none for the chat brain.")

    async def on_diary(update, context):
        if not is_allowed(update):
            return
        chat_id = update.effective_chat.id
        job = get_job(chat_id)
        if not job:
            await update.message.reply_text(
                "Set a job first with /job 501, then /diary to capture a site diary for it.")
            return
        set_diary_mode(chat_id, True)
        await update.message.reply_text(
            f"Diary mode on for {job}. Talk away - voice and text get buffered as site-diary notes. "
            "I build the Subcontractors Diary automatically at end of day (5pm Sydney). "
            "/notes to go back to General Notes.")

    async def on_notes(update, context):
        if not is_allowed(update):
            return
        chat_id = update.effective_chat.id
        SKILL_ACTIVE.discard(chat_id)
        set_diary_mode(chat_id, False)
        job = get_job(chat_id)
        if job:
            await update.message.reply_text(
                f"Back to notes mode for {job}. Voice and text go straight to General Notes.")
        else:
            await update.message.reply_text("Notes mode. Set a job with /job 501 to start capturing.")

    async def on_voice(update, context):
        if not is_allowed(update):
            return
        chat_id = update.effective_chat.id
        voice = update.message.voice or update.message.audio
        if not voice:
            return
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            tg_file = await context.bot.get_file(voice.file_id)
            audio = bytes(await tg_file.download_as_bytearray())
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"Could not fetch the voice note: {e}")
            return
        mimetype = getattr(voice, "mime_type", None) or "audio/ogg"
        job = get_job(chat_id)
        import sys
        print(f"[on_voice] audio_bytes: {len(audio)}, mimetype: {mimetype}, job: {job}", file=sys.stderr)
        # Diary mode: buffer the transcript for the end-of-day site-diary build.
        if diary_mode(chat_id):
            try:
                tr = voice_notes.transcribe_full(audio, mimetype)
            except Exception as e:  # noqa: BLE001
                await update.message.reply_text(f"Could not transcribe that: {e}")
                return
            transcript = tr["transcript"]
            if not transcript:
                await update.message.reply_text("Could not hear anything in that one - try again?")
                return
            # Write to the project's Voice Memo Log immediately, and SURFACE the
            # result. A silent failure here is what hid the broken DB ids before.
            memo_ok, memo_page_id, memo_err = voice_notes.write_to_voice_memo_log(
                transcript, project=job, captured_by=PM_NAME,
                duration_secs=tr.get("duration"), confidence=tr.get("confidence"))
            if not memo_ok:
                import sys
                print(f"[on_voice] voice_memo_log write FAILED: {memo_err}", file=sys.stderr)
            diary.append_note(transcript, job=job, voice_memo_page_id=memo_page_id)
            n = diary.buffer_count()
            memo_line = ("Voice Memo Log: saved" if memo_ok
                         else f"Voice Memo Log: NOT saved ({memo_err})")
            await update.message.reply_text(
                f"Diary note buffered for {job or 'no job'} ({n} today).\n"
                f"Captured: {len(transcript)} characters\n"
                f"{memo_line}\n"
                f"Preview: \"{transcript[:100]}...\"\n"
                "I build the site diary at end of day. /notes to switch back.")
            return
        # Notes mode: transcribe, structure, and log straight to General Notes.
        try:
            reply, _url = voice_notes.handle_voice(audio, mimetype, active_job=job)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"Could not log that: {e}")
            return
        await update.message.reply_text(reply)

    async def on_message(update, context):
        if not is_allowed(update):
            return  # silent ignore for anyone off the allowlist
        chat_id = update.effective_chat.id
        prompt = (update.message.text or "").strip()
        if not prompt:
            return
        job = get_job(chat_id)
        # Skill flow in progress (e.g. answering SWMS qualifying questions):
        # route to the brain regardless of any active job until it signals done.
        if chat_id in SKILL_ACTIVE:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            res = agent_core.ask(prompt, session_id=SESSIONS.get(chat_id))
            if await _deliver(update, chat_id, res):
                SKILL_ACTIVE.discard(chat_id)
            return
        # No active job: text goes to the chat brain (and any generated file
        # comes back via the [[SEND_FILE]] marker).
        if not job:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            res = agent_core.ask(prompt, session_id=SESSIONS.get(chat_id))
            await _deliver(update, chat_id, res)
            return
        # Job active + diary mode: buffer the text for the site diary.
        if diary_mode(chat_id):
            diary.append_note(prompt, job=job)
            n = diary.buffer_count()
            await update.message.reply_text(
                f"Diary note buffered for {job} ({n} today). Builds at end of day. /notes to switch back.")
            return
        # Job active + notes mode: structure and log straight to General Notes.
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        struct = voice_notes.structure_note(prompt, active_job=job)
        ok, _url, detail = voice_notes.log_note(struct)
        if ok:
            kind = "task" if struct.get("is_task", True) else "note"
            proj = struct.get("project") or job
            await update.message.reply_text(
                f"Logged ({proj}, {kind}, {struct.get('priority', 'Medium')}):\n"
                f"\"{struct.get('note')}\"")
        else:
            await update.message.reply_text(f"Note write failed: {detail}")

    # Skill commands: run a VPS workspace command via the brain. Each opens a
    # skill flow (SKILL_ACTIVE) so follow-up answers reach the brain even with a
    # job active. See reference/systems/agent-brain/vps-command-catalogue.md.
    SKILL_LABELS = {
        "swms": "SWMS", "ra": "risk assessment", "toolbox": "toolbox talk",
        "audit": "safety audit", "email": "email draft",
        "summarise": "document summary", "ask": "ask-projects query",
        "research": "research task",
    }

    async def run_skill(update, context):
        if not is_allowed(update):
            return
        chat_id = update.effective_chat.id
        cmd = (update.message.text or "").lstrip("/").split()[0].split("@")[0].lower()
        label = SKILL_LABELS.get(cmd, cmd)
        args = " ".join(context.args).strip() if context.args else ""
        detail = args or "(ask me what you need first)"
        instruction = (
            f"Run the {label} command from the VPS command catalogue "
            "(reference/systems/agent-brain/vps-command-catalogue.md). "
            f"Details: {detail}. If it is a safety document, ask the qualifying "
            "questions first, then generate it. When a file is produced, include "
            "[[SEND_FILE: <absolute path>]] so it reaches me, and finish with "
            "[[SKILL_DONE]]."
        )
        SKILL_ACTIVE.add(chat_id)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        res = agent_core.ask(instruction, session_id=SESSIONS.get(chat_id))
        if await _deliver(update, chat_id, res):
            SKILL_ACTIVE.discard(chat_id)

    async def _post_init(application):
        await application.bot.set_my_commands([
            BotCommand("ask", "Ask about your projects"),
            BotCommand("swms", "Draft a SWMS"),
            BotCommand("ra", "Draft a risk assessment"),
            BotCommand("toolbox", "Toolbox talk + Monday item"),
            BotCommand("audit", "Pre-start safety audit"),
            BotCommand("email", "Draft an email (not sent)"),
            BotCommand("summarise", "Summarise a document"),
            BotCommand("research", "Research a topic"),
            BotCommand("job", "Set active project for capture"),
            BotCommand("diary", "Site-diary capture mode"),
            BotCommand("notes", "Notes capture mode"),
            BotCommand("new", "Fresh conversation"),
            BotCommand("whoami", "Auth mode + chat id"),
        ])

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("new", on_new))
    app.add_handler(CommandHandler("whoami", on_whoami))
    app.add_handler(CommandHandler("job", on_job))
    app.add_handler(CommandHandler("diary", on_diary))
    app.add_handler(CommandHandler("notes", on_notes))
    for _skill_cmd in SKILL_LABELS:
        app.add_handler(CommandHandler(_skill_cmd, run_skill))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    print("Bot running. Press Ctrl-C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
