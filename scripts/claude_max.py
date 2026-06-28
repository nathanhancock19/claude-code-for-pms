#!/usr/bin/env python3
"""
claude_max.py - route a one-shot, structured Claude call through the Claude Max
subscription instead of the metered Anthropic API.

Workers that used to call the `anthropic` SDK with ANTHROPIC_API_KEY call
complete() / complete_json() here instead. It drives Claude Code headless
(`claude -p`) on the Max OAuth token, the same engine scripts/agent/agent_core.py
uses for the Telegram brain, but as a stateless single turn that returns text
(optionally parsed as JSON).

Billing guarantee
-----------------
This helper FORCES subscription auth. It strips ANTHROPIC_API_KEY from the child
environment on every call, so a worker can never silently fall back to metered
API credits. Auth resolves in this order:
  1. CLAUDE_CODE_OAUTH_TOKEN from the environment / .env (required on the VPS,
     which has no interactive login). Generate once with `claude setup-token`.
  2. The machine's interactive Claude Code login (works on a developer PC).
If neither is present the `claude` CLI fails loudly - by design, so cost can
never leak onto the API key.

Cost / rate-limit note
----------------------
Every `claude -p` call carries Claude Code's system-prompt overhead (~20k tokens
of cached context). For low-volume on-demand workers this is irrelevant. For a
high-volume classifier (email_router) prefer ONE call over a batch of items so
that overhead is amortised, not paid per item.

HARD RULE: no long dashes anywhere in this file or generated content.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = os.environ.get("CLAUDE_MAX_MODEL", "sonnet")
DEFAULT_TIMEOUT = int(os.environ.get("CLAUDE_MAX_TIMEOUT", "180"))


class ClaudeMaxError(RuntimeError):
    """Raised when the subscription call fails or returns no usable result."""


def _load_dotenv():
    """Self-load the workspace .env (zero dependency) so the token is set."""
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


def _resolve_bin():
    """Find the claude binary cross-platform (claude.cmd on Windows, claude on Linux)."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    return found or "claude"


def auth_mode():
    """Report how a call will authenticate, without making one."""
    _load_dotenv()
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription (CLAUDE_CODE_OAUTH_TOKEN)"
    return "subscription (interactive login) - no token in .env; add one for the VPS"


def _extract_json(text):
    """Pull a JSON object/array out of a model reply that may be fenced or chatty."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Fallback: slice from the first opening brace/bracket to its matching last one.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        end = t.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ClaudeMaxError(f"could not parse JSON from model reply: {text[:300]}")


def complete(prompt, system=None, model=None, timeout=None):
    """Run a single headless Claude turn on the Max subscription. Returns the text reply.

    prompt  - the user content (piped via stdin, so there is no argv length limit).
    system  - optional instructions; folded into the prompt for flag stability.
    model   - "sonnet" (default), "haiku", "opus", or a full model id.
    """
    _load_dotenv()
    env = dict(os.environ)
    # The billing guarantee: never let the child bill the metered API.
    env.pop("ANTHROPIC_API_KEY", None)

    full = prompt if not system else f"{system}\n\n---\n\n{prompt}"
    cmd = [
        _resolve_bin(),
        "-p",
        "--output-format", "json",
        "--model", model or DEFAULT_MODEL,
    ]
    # Run in a throwaway cwd so we do not auto-load this workspace's CLAUDE.md or
    # .mcp.json (which would add latency and start MCP servers we do not need).
    workdir = tempfile.mkdtemp(prefix="claude_max_")
    try:
        proc = subprocess.run(
            cmd,
            input=full,
            capture_output=True,
            text=True,
            env=env,
            cwd=workdir,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ClaudeMaxError(f"claude -p timed out after {timeout or DEFAULT_TIMEOUT}s")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode != 0:
        raise ClaudeMaxError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeMaxError(f"claude -p gave non-JSON output: {proc.stdout[:300]}")
    if envelope.get("is_error"):
        raise ClaudeMaxError(f"claude reported an error: {envelope.get('result')}")
    return envelope.get("result", "")


def complete_json(prompt, system=None, model=None, timeout=None):
    """Same as complete(), but parse the reply as JSON and return the object."""
    return _extract_json(complete(prompt, system=system, model=model, timeout=timeout))


if __name__ == "__main__":
    # Self-test: prove the subscription path works and JSON parsing is clean.
    print(f"auth: {auth_mode()}")
    obj = complete_json(
        'Reply with ONLY this JSON: {"ok": true, "src": "claude_max self-test"}'
    )
    print(f"parsed: {obj}")
    assert obj.get("ok") is True, obj
    print("claude_max OK")
