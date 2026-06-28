#!/usr/bin/env python3
"""
vps_reminders.py - simple scheduled-reminder engine for the Dunsteel VPS.

Architecture (per Nathan's rule): anything schedule-based that cannot rely on
the desktop runs on the always-on VPS as a plain cron + this script, synced
from git. n8n is reserved for genuinely multi-step workflows; a reminder is a
one-step timer, so it lives here, not in n8n.

How it runs:
  - A single daily cron entry on the VPS runs this script once a day.
  - The script reads reminders.json (alongside this file), works out which
    reminders are DUE today (weekday match + inside the active window), and
    sends each one. New reminders are just new JSON entries - no new cron lines.

Channel:
  - Email to self via SMTP, using SMTP_* values from the VPS .env.
  - If SMTP is not configured, the script runs in LOG mode: it prints/logs the
    reminders that would have been sent and exits 0. This keeps it safe to run
    before the mail credential is wired in (the channel is currently parked).

Usage:
  python3 scripts/vps_reminders.py            # send due reminders (or log if no SMTP)
  python3 scripts/vps_reminders.py --dry-run  # never send; just print what is due
  python3 scripts/vps_reminders.py --all      # ignore schedule; show every active reminder

Env (from .env next to the repo root, loaded with a zero-dependency loader -
python-dotenv is not assumed):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, SMTP_FROM
  REMINDER_EMAIL_TO  (default recipient if a reminder has no explicit "to")

HARD RULE: no long dashes anywhere in this file or its output. Use a hyphen,
a colon, or restructure.
"""

import argparse
import datetime
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
CONFIG = HERE / "reminders.json"
LOG_FILE = HERE / "vps_reminders.log"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load_dotenv():
    """Populate os.environ from the repo-root .env (zero dependency).

    Does not overwrite a value already set in the real environment.
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


def load_reminders():
    if not CONFIG.exists():
        print(f"No reminders config at {CONFIG}", file=sys.stderr)
        return []
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return data.get("reminders", [])


def is_due(reminder, today):
    """True if this reminder should fire today (weekday match + active window)."""
    af = reminder.get("active_from")
    au = reminder.get("active_until")
    if af and today < datetime.date.fromisoformat(af):
        return False
    if au and today > datetime.date.fromisoformat(au):
        return False
    days = [d.lower()[:3] for d in reminder.get("weekdays", [])]
    if not days:
        return True  # no weekday filter means every day inside the window
    return WEEKDAYS[today.weekday()] in days


def smtp_config():
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASS", ""),
        "sender": os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
    }


def send_email(cfg, to_addr, subject, body):
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
        server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(msg)


def log_line(text):
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} | {text}\n")
    except OSError:
        pass
    print(f"{stamp} | {text}")


def main():
    ap = argparse.ArgumentParser(description="Dunsteel VPS reminder engine")
    ap.add_argument("--dry-run", action="store_true", help="never send; print what is due")
    ap.add_argument("--all", action="store_true", help="ignore schedule; show every active reminder")
    args = ap.parse_args()

    load_dotenv()
    today = datetime.date.today()
    reminders = load_reminders()
    if not reminders:
        log_line("No reminders configured. Nothing to do.")
        return 0

    cfg = smtp_config()
    default_to = os.environ.get("REMINDER_EMAIL_TO", "")
    mode = "dry-run" if args.dry_run else ("email" if cfg else "log")
    log_line(f"Run start. mode={mode} today={today.isoformat()} reminders={len(reminders)}")

    due = []
    for r in reminders:
        if args.all or is_due(r, today):
            due.append(r)

    if not due:
        log_line("No reminders due today.")
        return 0

    for r in due:
        to_addr = r.get("to") or default_to
        subject = r.get("subject", "Dunsteel reminder")
        body = r.get("message", "")
        until = r.get("active_until")
        footer = f"\n\n(This reminder repeats until {until}. Remove it from reminders.json once the item is done.)" if until else ""
        full_body = body + footer

        if args.dry_run or mode == "log":
            why = "DRY-RUN" if args.dry_run else "LOG MODE (no SMTP configured)"
            log_line(f"{why}: would email '{subject}' to '{to_addr or '[no recipient set]'}'")
            continue

        if not to_addr:
            log_line(f"SKIP '{subject}': no recipient (set REMINDER_EMAIL_TO or reminder.to)")
            continue
        try:
            send_email(cfg, to_addr, subject, full_body)
            log_line(f"SENT '{subject}' to {to_addr}")
        except Exception as exc:  # noqa: BLE001 - report any send failure, keep going
            log_line(f"FAILED '{subject}' to {to_addr}: {exc}")

    log_line("Run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
