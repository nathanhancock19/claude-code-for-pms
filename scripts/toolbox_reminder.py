#!/usr/bin/env python3
"""
toolbox_reminder.py - weekly toolbox-talk chase for Dunsteel PMs.

Runs once per weekday on the VPS cron. Each run:
  1. Works out the current Mon-Sun week.
  2. Reads the Monday "Toolbox Talk" board and finds which PMs already have a
     talk dated within this week (by the PM single-select label).
  3. On Monday, emails every in-scope PM a start-of-week reminder.
     On Tue-Fri, emails only PMs who still have no talk logged this week.
  4. Sends each PM at most once per day, and never again once they are done.

Send path: Gmail OAuth (GMAIL_SEND_* in .env), the same identity the safety PDF
dispatcher uses. CC goes to the oversight address (Casey).

State: toolbox_reminder_state.json (next to this file) holds the ISO week and the
last send date per PM, so re-running the same day is a no-op and a new week resets.

Usage:
  python3 scripts/toolbox_reminder.py              # send due reminders
  python3 scripts/toolbox_reminder.py --dry-run    # scan + print, never send
  python3 scripts/toolbox_reminder.py --test-to EMAIL   # send one sample to EMAIL only
  python3 scripts/toolbox_reminder.py --date 2026-06-22  # pretend today is this date

HARD RULE: no long dashes anywhere in this file or its output.
"""

import argparse
import base64
import datetime
import json
import os
import sys
import urllib.request
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
STATE_FILE = HERE / "toolbox_reminder_state.json"
LOG_FILE = HERE / "toolbox_reminder.log"

sys.path.insert(0, str(HERE))
import dunsteel_projects as projects  # noqa: E402
from dunsteel_monday import MondayClient  # noqa: E402

TOOLBOX_BOARD = YOUR_TOOLBOX_BOARD_ID
PM_COL = "single_select82oifav"   # Project Manager status column
DATE_COL = "date"

# In-scope PMs to chase. These MUST match the board's Project Manager dropdown
# labels (column single_select82oifav on board YOUR_TOOLBOX_BOARD_ID): Sam, Nathan, Riley,
# Jordan, Alex, Casey. Alex is excluded (minimal jobs); Casey is oversight.
IN_SCOPE_PMS = ["Jordan", "Sam", "Riley", "Nathan"]
CC_LABEL = None  # no CC

# Toolbox-talk submission form (Monday form).
FORM_LINK = "https://forms.monday.com/forms/YOUR_MONDAY_FORM_ID?r=use1"
SIGN_OFF = ("This is an automated WHS reminder, sent each week until your toolbox "
            "talk for the week is recorded.")


def load_dotenv():
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def log_line(text):
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} | {text}\n")
    except OSError:
        pass
    print(f"{stamp} | {text}")


def week_bounds(today):
    """Monday and Sunday (inclusive) of the week containing `today`."""
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=6)


def iso_week(today):
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------------------
# Board scan
# ---------------------------------------------------------------------------

def done_pms_this_week(m, monday, sunday):
    """Return (done_labels:set, untagged_count:int) for the current week.

    A PM is done if any board item has their PM label and a Date within the week.
    Items in-week with no PM label are counted separately so they can be flagged.
    """
    done, untagged = set(), 0
    mon_iso, sun_iso = monday.isoformat(), sunday.isoformat()
    for it in m.board_items(TOOLBOX_BOARD):
        d = (it["columns"].get(DATE_COL) or "").strip()
        if not d or not (mon_iso <= d <= sun_iso):
            continue
        pm = (it["columns"].get(PM_COL) or "").strip()
        if pm:
            done.add(pm)
        else:
            untagged += 1
    return done, untagged


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def gmail_send(to_email, cc_email, subject, body, html=None):
    """Send an email via Gmail OAuth (no attachment). If `html` is given it is
    added as the preferred alternative so the text flows naturally and the form
    link is clickable; the plain `body` stays as the fallback."""
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
    if cc_email:
        em["Cc"] = cc_email
    em["Subject"] = subject
    em.set_content(body)
    if html:
        em.add_alternative(html, subtype="html")
    raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(),
        headers={"Authorization": "Bearer " + access, "Content-Type": "application/json"},
        method="POST")
    urllib.request.urlopen(req, timeout=30)


def subject_for(monday, is_monday):
    wc = monday.strftime("%#d %B %Y") if sys.platform == "win32" else monday.strftime("%-d %B %Y")
    if is_monday:
        return f"Weekly toolbox talk - week commencing {wc}"
    return f"Toolbox talk outstanding - week commencing {wc}"


def body_for(first_name, monday, is_monday):
    wc = monday.strftime("%#d %B %Y") if sys.platform == "win32" else monday.strftime("%-d %B %Y")
    if is_monday:
        main = (
            f"This is your reminder to run your toolbox talk for the week commencing "
            f"{wc} with your crew and log it on the toolbox talk form: {FORM_LINK}"
        )
    else:
        main = (
            f"Your toolbox talk for the week commencing {wc} has not been recorded "
            f"yet. Please complete it and log it on the toolbox talk form: {FORM_LINK}"
        )
    return f"Hi {first_name},\n\n{main}\n\n{SIGN_OFF}"


def html_body_for(first_name, monday, is_monday):
    """HTML version so the client flows the text (no hard 78-char wrapping) and
    the form link is a clickable link."""
    wc = monday.strftime("%#d %B %Y") if sys.platform == "win32" else monday.strftime("%-d %B %Y")
    link = f'<a href="{FORM_LINK}">toolbox talk form</a>'
    if is_monday:
        main = (f"This is your reminder to run your toolbox talk for the week commencing "
                f"{wc} with your crew and log it on the {link}.")
    else:
        main = (f"Your toolbox talk for the week commencing {wc} has not been recorded "
                f"yet. Please complete it and log it on the {link}.")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;">'
        f"<p>Hi {first_name},</p>"
        f"<p>{main}</p>"
        f"<p>{SIGN_OFF}</p>"
        "</div>"
    )


def name_for_email(email):
    """First-name label for a known PM email, else 'there' (test fallback)."""
    e = (email or "").strip().lower()
    for key, addr in projects.PM_EMAIL.items():
        if addr.lower() == e:
            return projects.PM_LABEL.get(key, "there")
    return "there"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"week": "", "sent": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Weekly toolbox-talk reminder/chase for PMs")
    ap.add_argument("--dry-run", action="store_true", help="scan and print, never send")
    ap.add_argument("--test-to", help="send one sample reminder to this address only, then exit")
    ap.add_argument("--date", help="pretend today is YYYY-MM-DD (testing)")
    args = ap.parse_args()

    load_dotenv()
    today = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    monday, sunday = week_bounds(today)
    is_monday = today.weekday() == 0
    cc_email = projects.pm_email(CC_LABEL) if CC_LABEL else None

    # Test send: one sample to a chosen address, no board scan, no state.
    # Personalises by the recipient's email so the name variable can be verified.
    if args.test_to:
        name = name_for_email(args.test_to)
        subj = "[TEST] " + subject_for(monday, is_monday)
        gmail_send(args.test_to, None, subj,
                   body_for(name, monday, is_monday),
                   html_body_for(name, monday, is_monday))
        log_line(f"TEST email sent to {args.test_to} (Hi {name})")
        return 0

    # Mon-Fri only.
    if today.weekday() > 4:
        log_line(f"{today} is a weekend; no chase. Exit.")
        return 0

    m = MondayClient()
    done, untagged = done_pms_this_week(m, monday, sunday)
    log_line(f"Week {iso_week(today)} ({monday}..{sunday}). Done: {sorted(done) or 'none'}. "
             f"Untagged in-week items: {untagged}.")

    state = load_state()
    if state.get("week") != iso_week(today):
        state = {"week": iso_week(today), "sent": {}}

    for pm in IN_SCOPE_PMS:
        is_done = pm in done
        # Monday: remind everyone. Tue-Fri: only those outstanding.
        should = is_monday or not is_done
        if is_done and not is_monday:
            continue
        if state["sent"].get(pm) == today.isoformat():
            log_line(f"  {pm}: already emailed today; skip.")
            continue
        if not should:
            continue
        to_email = projects.pm_email(pm)
        subj = subject_for(monday, is_monday)
        body = body_for(pm, monday, is_monday)
        html = html_body_for(pm, monday, is_monday)
        tag = "reminder(Mon)" if is_monday else "chase"
        if args.dry_run:
            log_line(f"  DRY-RUN {tag}: would email {pm} <{to_email}> cc {cc_email}"
                     f"{' [already done]' if is_done else ''}")
            continue
        try:
            gmail_send(to_email, cc_email, subj, body, html)
            state["sent"][pm] = today.isoformat()
            log_line(f"  SENT {tag} to {pm} <{to_email}>")
        except Exception as e:  # noqa: BLE001
            log_line(f"  FAILED {pm} <{to_email}>: {e}")

    if not args.dry_run:
        save_state(state)
    if untagged:
        log_line(f"NOTE: {untagged} toolbox item(s) this week have no PM tag; "
                 f"a PM with an untagged item may be chased in error. Tag them on the board.")
    log_line("Run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
