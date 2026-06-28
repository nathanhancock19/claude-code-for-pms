#!/usr/bin/env python3
"""
diary_eod_cron.py - the end-of-day site diary build, run by VPS cron.

Builds the day's site diary from the buffered voice notes and pushes the summary
to Nathan's Telegram. If nothing was captured, sends a gentle reminder instead.

Cron (VPS, 17:00 Sydney = 07:00 UTC):
    0 7 * * * cd /root/claude-workspace-template && .venv/bin/python scripts/agent/diary_eod_cron.py >> /root/diary_eod.log 2>&1
"""

import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diary  # noqa: E402


def send(text):
    diary._load_dotenv()
    token = os.environ.get("AGENT_BOT_TOKEN") or os.environ.get("DUNSTEEL_BOT_TOKEN")
    chat = os.environ.get("AGENT_ALLOWED_CHAT_IDS") or os.environ.get("DUNSTEEL_CHAT_ID")
    if not token or not chat:
        print("no telegram creds; printing instead:\n", text)
        return
    chat = chat.split(",")[0].strip()
    for i in range(0, len(text), 3800):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 3800],
                                       "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST"),
                timeout=15)
        except Exception as e:  # noqa: BLE001
            print("telegram send failed:", e)


def main():
    diary._load_dotenv()
    day = diary._today()
    if diary.buffer_count(day) == 0:
        send("End of day: no site diary notes captured today. "
             "Send a voice note if you were on site and I will log it.")
        return
    summary = diary.run_eod(day)   # force=False: skips if already built via /diary
    send(summary)


if __name__ == "__main__":
    main()
