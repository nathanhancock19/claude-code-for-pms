#!/usr/bin/env python3
"""
agent_core.py - the Dunsteel operations brain.

Drives Claude Code headless (`claude -p`) as an agent, authenticated with
Nathan's Claude Max subscription via CLAUDE_CODE_OAUTH_TOKEN (generated once with
`claude setup-token`). This is the subscription-powered, Opus-grade brain that
both the Telegram bot and (later) the AIOS web chat call.

Why the CLI and not the Agent SDK Python types: same engine, same subscription
auth, but stable and testable today. agent_core isolates every CLI flag in one
place so a future swap to claude-agent-sdk is a single-file change.

Public API:
    ask(prompt, session_id=None) -> AskResult(text, session_id, ok, raw)

The brain runs with this workspace as its working directory, so it auto-loads
CLAUDE.md and the project .mcp.json (Notion + n8n), and can reach every script
and brief. The agent system delta lives in system_prompt.md.

Env:
    CLAUDE_CODE_OAUTH_TOKEN   Max subscription token (from `claude setup-token`).
                              If absent, the CLI falls back to ANTHROPIC_API_KEY.
    AGENT_MODEL               Model alias. Default "opus".
    AGENT_PERMISSION_MODE     Claude Code permission mode for unattended runs.
                              Default "acceptEdits" (no interactive prompts).
    AGENT_ALLOWED_TOOLS       Comma list passed to --allowedTools. Default below.
    AGENT_TIMEOUT             Per-turn timeout seconds. Default 240.
    CLAUDE_BIN                Path to the claude binary. Default "claude".

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_FILE = AGENT_DIR / "system_prompt.md"

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "opus")
DEFAULT_PERMISSION_MODE = os.environ.get("AGENT_PERMISSION_MODE", "acceptEdits")
DEFAULT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "240"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

# Tools the brain may use unattended. Read + analysis + internal writes + the
# connected MCP servers. External sends are gated by the system prompt, not by
# tool removal, so the brain can still DRAFT replies (writing to outputs/).
DEFAULT_ALLOWED_TOOLS = os.environ.get(
    "AGENT_ALLOWED_TOOLS",
    "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,"
    "mcp__notion,mcp__n8n-dunsteel",
)


@dataclass
class AskResult:
    text: str
    session_id: str | None
    ok: bool
    raw: dict = field(default_factory=dict)


def _load_dotenv():
    """Self-load the workspace .env (zero dependency) so the token + keys are set."""
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _build_command(prompt: str, session_id: str | None) -> list[str]:
    cmd = [
        CLAUDE_BIN,
        "-p", prompt,
        "--output-format", "json",
        "--model", DEFAULT_MODEL,
        "--permission-mode", DEFAULT_PERMISSION_MODE,
        "--allowedTools", DEFAULT_ALLOWED_TOOLS,
        "--append-system-prompt", _system_delta(),
    ]
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def _system_delta() -> str:
    if SYSTEM_PROMPT_FILE.exists():
        return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    return ""


def auth_mode() -> str:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription (CLAUDE_CODE_OAUTH_TOKEN)"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api key (ANTHROPIC_API_KEY) - fallback, not the Max plan"
    return "NONE - run `claude setup-token` first"


def ask(prompt: str, session_id: str | None = None) -> AskResult:
    """Send one turn to the brain. Returns text + the session id to reuse next."""
    _load_dotenv()
    if shutil.which(CLAUDE_BIN) is None and not Path(CLAUDE_BIN).exists():
        return AskResult(
            text=f"Claude Code binary not found ({CLAUDE_BIN}). Install it or set CLAUDE_BIN.",
            session_id=session_id, ok=False,
        )
    cmd = _build_command(prompt, session_id)
    try:
        proc = subprocess.run(
            cmd, cwd=str(WORKSPACE), capture_output=True, text=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return AskResult(text="(timed out - the task ran long, try a tighter ask)",
                         session_id=session_id, ok=False)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        return AskResult(text=f"(brain error) {err}", session_id=session_id, ok=False)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Fall back to raw stdout if not JSON (older CLI / streamed output).
        return AskResult(text=proc.stdout.strip(), session_id=session_id, ok=True)

    text = data.get("result") or data.get("text") or ""
    new_sid = data.get("session_id") or session_id
    is_error = bool(data.get("is_error"))
    return AskResult(text=text.strip(), session_id=new_sid, ok=not is_error, raw=data)


if __name__ == "__main__":
    _load_dotenv()
    print(f"Auth mode: {auth_mode()}")
    q = " ".join(sys.argv[1:]) or "In one line, who are you and what can you do for me?"
    res = ask(q)
    print(f"\nSession: {res.session_id}\nOK: {res.ok}\n\n{res.text}")
