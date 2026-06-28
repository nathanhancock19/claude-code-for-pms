#!/usr/bin/env python3
"""
pm_daily_brief.py - VPS cron: send each PM their daily brief via their own bot.

For each PM instance, runs `/daily-brief` headless in that PM's workspace (so it
is scoped to their projects and billed to the shared Dunsteel Max subscription),
then delivers the result to that PM's chat via that PM's bot token. The token and
chat id are read from the PM's own workspace .env, so no secret is duplicated
here. Stagger the per-PM cron times so two briefs never run at once (the only
predictable RAM spike on the 8GB VPS).

Instances are listed in scripts/provision/pm_instances.json (gitignored; see
pm_instances.example.json). Shape: {"<pm_key>": {"workspace": "/path/on/vps"}}.

DRY-RUN by default: prints the plan, runs nothing, sends nothing.

    python scripts/pm_daily_brief.py --pm alex            # dry-run one PM
    python scripts/pm_daily_brief.py --pm alex --apply     # run + send
    python scripts/pm_daily_brief.py --all --apply         # every instance

HARD RULE: no long dashes anywhere in this file.
"""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
INSTANCES = WORKSPACE / "scripts" / "provision" / "pm_instances.json"
TG_LIMIT = 4096


def _load_env(path):
    out = {}
    if not Path(path).exists():
        return out
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _run_brief(ws):
    claude = shutil.which("claude") or "claude"
    proc = subprocess.run(
        [claude, "-p", "/daily-brief"], cwd=str(ws),
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "claude failed")[:500]
    return proc.stdout.strip(), None


def _tg_send(token, chat_id, text):
    for i in range(0, len(text), TG_LIMIT):
        chunk = text[i:i + TG_LIMIT]
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()


def _process(pm, cfg, apply):
    ws = Path(cfg["workspace"]).resolve()
    env = _load_env(ws / ".env")
    token = env.get("AGENT_BOT_TOKEN")
    chat = (env.get("AGENT_ALLOWED_CHAT_IDS") or "").split(",")[0].strip()
    print(f"\n[{pm}] workspace={ws}")
    print(f"  token={'set' if token else 'MISSING'}  chat_id={chat or 'MISSING'}")
    if not apply:
        print("  DRY-RUN: would run /daily-brief here and send to the chat above.")
        return True
    if not (token and chat):
        print("  SKIP: missing bot token or chat id in the PM .env")
        return False
    brief, err = _run_brief(ws)
    if err:
        print(f"  brief FAILED: {err}")
        return False
    _tg_send(token, chat, brief)
    print(f"  sent ({len(brief)} chars)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pm", help="one PM key")
    ap.add_argument("--all", action="store_true", help="every instance")
    ap.add_argument("--instances", help="instances json (default: provision/pm_instances.json)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    path = Path(args.instances) if args.instances else INSTANCES
    if not path.exists():
        sys.exit(f"No instances file: {path}. Copy pm_instances.example.json and fill it.")
    instances = json.loads(path.read_text(encoding="utf-8"))

    if args.pm:
        targets = {args.pm: instances[args.pm]} if args.pm in instances else {}
        if not targets:
            sys.exit(f"PM '{args.pm}' not in {path}")
    elif args.all:
        targets = instances
    else:
        sys.exit("Pass --pm <key> or --all")

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}   instances: {', '.join(targets)}")
    ok = sum(1 for pm, cfg in targets.items() if _process(pm, cfg, args.apply))
    print(f"\nDone: {ok}/{len(targets)} ok")


if __name__ == "__main__":
    main()
