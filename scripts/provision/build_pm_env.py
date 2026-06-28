#!/usr/bin/env python3
"""
build_pm_env.py - assemble a PM's .env from .env.pm-template.

Copies the SHARED block verbatim from the shared Dunsteel .env, fills the PER-PM
block from the flags below, and writes the PM's .env into their clone. Secret
values are NEVER printed (dry-run shows masked summaries only). Local-only AIOS
keys are left blank for the PM to add on their own machine.

DRY-RUN by default: shows which keys would be set (masked). Pass --apply to write.

    python scripts/provision/build_pm_env.py --pm alex --dest /path/to/alex-clone \\
        --bot-token <token> --chat-id 12345678
    (add --apply to write)

HARD RULE: no long dashes anywhere in this file.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import dunsteel_projects as dp  # noqa: E402

TEMPLATE = common.WORKSPACE / ".env.pm-template"


def _mask(v):
    if not v:
        return "(blank)"
    return v[:4] + "..." + v[-2:] if len(v) > 8 else "***"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pm", required=True)
    ap.add_argument("--dest", required=True, help="path to the PM's workspace clone")
    ap.add_argument("--bot-token", help="this PM's Telegram bot token")
    ap.add_argument("--chat-id", help="this PM's allowed chat id(s), comma-separated")
    ap.add_argument("--voice-projects", default="", help="optional override; else registry-derived")
    ap.add_argument("--shared-env", help="path to the shared Dunsteel .env (default: this repo's .env)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pm_key = dp.pm_key(args.pm)
    if not pm_key:
        sys.exit(f"Unknown PM '{args.pm}'. Known: {', '.join(dp.pm_keys())}")
    dest = Path(args.dest).resolve()
    if dest == common.WORKSPACE:
        sys.exit("Refusing to write into the current repo. --dest must be the PM's clone.")

    shared = common.load_env(args.shared_env)   # the shared Dunsteel keys
    perpm = {
        "AGENT_BOT_TOKEN": args.bot_token or "",
        "AGENT_ALLOWED_CHAT_IDS": args.chat_id or "",
        "AGENT_PM_NAME": dp.pm_label(pm_key),
        "AGENT_VOICE_PROJECTS": args.voice_projects or "",
    }

    if args.apply:
        missing = [k for k in ("AGENT_BOT_TOKEN", "AGENT_ALLOWED_CHAT_IDS") if not perpm[k]]
        if missing:
            sys.exit(f"Missing required per-PM values for --apply: {', '.join(missing)} "
                     "(pass --bot-token / --chat-id)")

    out_lines, summary = [], []
    for raw in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.strip().startswith("#"):
            k, _, v = raw.partition("=")
            k = k.strip()
            if v.strip() == "__SHARED__":
                val = shared.get(k, "")
                if not val:
                    summary.append(f"  WARN shared key not in shared .env: {k}")
                out_lines.append(f"{k}={val}")
                summary.append(f"  {k} = {_mask(val)} (shared)")
                continue
            if v.strip() == "__PERPM__":
                val = perpm.get(k, "")
                out_lines.append(f"{k}={val}")
                summary.append(f"  {k} = {_mask(val) if 'TOKEN' in k else (val or '(blank)')} (per-PM)")
                continue
            if v.strip() == "__MANUAL__":
                # Multi-line / hand-copied values (e.g. the inline Google JSON).
                out_lines.append(f"{k}=")
                summary.append(f"  {k} = (blank) MANUAL: copy by hand from the shared .env")
                continue
        out_lines.append(raw)

    print(f"PM: {pm_key} ({dp.pm_label(pm_key)})")
    print(f"Dest .env: {dest / '.env'}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")
    print("\n".join(summary))

    if args.apply:
        if not dest.exists():
            sys.exit(f"--dest does not exist: {dest}")
        (dest / ".env").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"\nWrote {dest / '.env'} (gitignored). Local AIOS keys left blank for the PM.")
    else:
        print("\nDRY-RUN: no file written. Re-run with --apply.")


if __name__ == "__main__":
    main()
