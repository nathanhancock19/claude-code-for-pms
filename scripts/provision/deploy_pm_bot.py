#!/usr/bin/env python3
"""
deploy_pm_bot.py - stand up a PM's always-on Telegram bot on the VPS.

Run this ON the VPS. It installs an isolated systemd unit (dunsteel-bot-<pm>) that
runs that PM's telegram_bot.py from their own checkout, reading their own .env.
Fully separate from Nathan's process - it never touches Nathan's unit.

DRY-RUN by default: prints the unit file and the exact commands. Pass --apply to
write the unit, daemon-reload, and enable+start it (needs sudo / root).

    python scripts/provision/deploy_pm_bot.py --pm alex --workspace /opt/dunsteel/alex
    (add --apply on the VPS to install)

HARD RULE: no long dashes anywhere in this file.
"""

import argparse
import subprocess
import sys
from pathlib import Path

UNIT_TMPL = """[Unit]
Description=Dunsteel Telegram bot ({pm})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={workspace}
ExecStart={python} {workspace}/scripts/agent/telegram_bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pm", required=True, help="canonical PM key, e.g. alex")
    ap.add_argument("--workspace", required=True, help="the PM's checkout dir on the VPS")
    ap.add_argument("--python", default="python3", help="python (ideally the PM venv's python)")
    ap.add_argument("--user", default="root", help="VPS user to run the bot as")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ws = Path(args.workspace)
    ws_posix = ws.as_posix().rstrip("/")
    unit_name = f"dunsteel-bot-{args.pm}.service"
    unit_path = f"/etc/systemd/system/{unit_name}"
    bot = ws / "scripts" / "agent" / "telegram_bot.py"
    env = ws / ".env"

    unit = UNIT_TMPL.format(pm=args.pm, user=args.user, workspace=ws_posix, python=args.python)

    print(f"PM: {args.pm}   workspace: {ws_posix}")
    print(f"Unit: {unit_path}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")
    print("Pre-flight:")
    print(f"  bot script exists : {bot.exists()}  ({bot.as_posix()})")
    print(f"  .env exists       : {env.exists()}  ({env.as_posix()})")
    print("\n--- unit file ---")
    print(unit)
    print("--- commands ---")
    print(f"  sudo tee {unit_path} < unit")
    print("  sudo systemctl daemon-reload")
    print(f"  sudo systemctl enable --now {unit_name}")
    print(f"  systemctl status {unit_name}")
    print("\nNote: this does NOT touch Nathan's bot unit (dunsteel-brain.service).")
    print("  Verify his is still active:  systemctl is-active dunsteel-brain.service")

    if not args.apply:
        print("\nDRY-RUN: nothing installed. Re-run with --apply on the VPS (sudo).")
        return

    if not bot.exists() or not env.exists():
        sys.exit("Refusing to install: bot script or .env missing in the workspace.")
    Path(unit_path).write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", unit_name], check=True)
    subprocess.run(["systemctl", "status", "--no-pager", unit_name], check=False)
    print(f"\nInstalled and started {unit_name}.")


if __name__ == "__main__":
    main()
