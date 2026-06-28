#!/usr/bin/env python3
"""
smoke_test.py - prove the brain works before wiring Telegram.

Checks, in order:
  1. Which auth mode is active (want: subscription via CLAUDE_CODE_OAUTH_TOKEN).
  2. A one-shot question answered by the brain (proves Opus + auth).
  3. A second turn reusing the session id (proves multi-turn memory).
  4. A tool-use ask (proves it can actually read the workspace).

Run:
    python scripts/agent/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_core  # noqa: E402


def main():
    agent_core._load_dotenv()
    print(f"1. Auth mode: {agent_core.auth_mode()}")
    if "NONE" in agent_core.auth_mode():
        sys.exit("   Stop: run `claude setup-token` to authenticate with your Max plan first.")

    print("2. One-shot question ...")
    r1 = agent_core.ask("In one short line: who are you and whose workspace is this?")
    print(f"   ok={r1.ok} session={r1.session_id}")
    print(f"   > {r1.text[:400]}")
    if not r1.ok:
        sys.exit("   Stop: first turn failed. See error above.")

    print("3. Follow-up (same session, tests memory) ...")
    r2 = agent_core.ask("What did I just ask you?", session_id=r1.session_id)
    print(f"   ok={r2.ok} session={r2.session_id}")
    print(f"   > {r2.text[:400]}")

    print("4. Tool-use ask (reads the workspace) ...")
    r3 = agent_core.ask(
        "List the project ids that have a brief in reference/projects/. "
        "Just the numbers, one line.",
        session_id=r2.session_id,
    )
    print(f"   ok={r3.ok}")
    print(f"   > {r3.text[:400]}")

    print("\nIf all four returned sensible answers, the brain is ready for Telegram.")


if __name__ == "__main__":
    main()
