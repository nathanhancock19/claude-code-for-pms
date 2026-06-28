# Dunsteel Operations Brain

An interactive AI brain you talk to over Telegram (and later the AIOS web chat).
It runs Claude Code on your **Max subscription** (Opus), inside this workspace, so
it has your full context and can actually act: run the email router, read briefs,
write to Notion, draft documents. It does not auto-send anything externally.

```
Telegram  ->  telegram_bot.py  ->  agent_core.ask()  ->  claude -p (Opus, Max token)
                                                          tools: Bash, files, Notion + n8n MCP
```

## One-time setup

### 1. Authenticate with your Max plan (you must do this - it is interactive)
On the machine that will run the brain:
```
claude setup-token
```
This opens a browser, logs into your Claude account, and prints a one-year token.
Opt into the **Agent SDK monthly credit** when prompted (Max 20x = $200/mo, separate
from your interactive usage). Put the token in the workspace `.env`:
```
CLAUDE_CODE_OAUTH_TOKEN=<the token from setup-token>
```
Without this the brain falls back to `ANTHROPIC_API_KEY` (per-token billed, not your
Max plan). Check which mode is active any time with `python scripts/agent/agent_core.py`.

### 2. Create the Telegram bot
- Message **@BotFather** on Telegram, `/newbot`, follow the prompts, copy the token.
- Message your new bot once, then get your chat id (e.g. message **@userinfobot**, or
  run the bot and use `/whoami`).
- Add to `.env`:
```
AGENT_BOT_TOKEN=<bot token from BotFather>
AGENT_ALLOWED_CHAT_IDS=<your telegram chat id>
```
The bot ignores every chat id not on `AGENT_ALLOWED_CHAT_IDS`. Do not leave it blank.

### 3. Install the Telegram dependency
```
pip install -r scripts/agent/requirements.txt
```
(The brain itself needs no Python packages - it uses the `claude` CLI, already
installed here. On a fresh VPS you also need Node + `npm i -g @anthropic-ai/claude-code`.)

## Prove it works (before Telegram)
```
python scripts/agent/smoke_test.py
```
Four checks: auth mode, a one-shot Opus answer, a follow-up that tests memory, and a
tool-use ask that reads the workspace. If all four look sensible, you are ready.

## Run the Telegram bot
```
python scripts/agent/telegram_bot.py
```
Then message your bot. Try:
- "what is hot on 505 this week?" (it can run the email router)
- "summarise the latest 501 variation thread"
- "/new" to start a fresh conversation, "/whoami" to see auth + your chat id

## Capture: notes by default, site diary on demand

Set a job, then just talk or type. Two capture modes per chat:

**Notes mode (default whenever a job is set).** With **`/job 501`** active, every
voice note and every typed message goes **straight to the Notion General Notes
table** for that project: transcribed (Deepgram), lightly structured by Claude
(project, priority, task-vs-info), and written as a row. The chat brain is paused
while a job is set - send **`/job none`** to clear the job and text the brain again.
Voice notes with no job set still log to General Notes (project auto-detected from
keywords, or blank).

**Diary mode (opt-in).** Send **`/diary`** to switch the active job into site-diary
capture. Now voice + text get **buffered** instead of logged. Talk naturally through
the day - who is on which job doing what, several projects in one note if you like.
At end of day the whole buffer is analysed together, split by project, and written
as one Dunsteel Subcontractors Diary entry per project; tasks you mention in passing
are pulled out to General Notes. Send **`/notes`** to switch back.

- Diary mode auto-resets to notes mode the next day (the flag is date-stamped).
- **Auto build** runs on the VPS at **17:00 Sydney** (cron -> `diary_eod_cron.py`)
  and pushes the summary to Telegram. There is no manual build command - the cron
  is the single build path. If nothing was captured it sends a reminder.
- Diary entries land with Status = Not Invoiced for you to review / edit in Notion.

Diary-mode example: *"Shane, Leo and Andreas at Riverside on Stair 2; three from
a rigging crew on Zone B canopies at Parkview. Remind me to order chemset for
501."* -> a 501 diary entry (Shane/Leo/Andreas, Stair 2), a 502 entry (rigging
crew x3, Zone B), and a General Notes task "Order chemset" on 501.

General Notes DB: `YOUR_GENERAL_NOTES_DB_ID`. Diary targets:
`outputs/internal/site-diary-db-registry.json`. Runs on `ANTHROPIC_API_KEY` +
`DEEPGRAM_API_KEY` + `NOTION_API_KEY`. Workers: `voice_notes.py` (General Notes),
`diary.py` + `diary_eod_cron.py` (site diary).

## Run workspace commands from the phone (VPS command set)

The brain can run the reusable workspace commands that work on the VPS (no S:
drive, no Office) and send the finished PDF back to the chat. In scope: SWMS,
risk assessment, toolbox talk, safety audit, email draft, summarise,
ask-projects, research. Out of scope (PC only, need S:/Word/Excel): variation,
bolt-cert, find-on-sdrive, new-project, variation-pack.

Tap a command (they appear in the Telegram menu) or just ask in plain language:

- `/swms 501 Stair 6 reinstatement` then answer the qualifying questions
- `/toolbox 501` or `/toolbox 501 --topic silica`
- `/audit 501 L6 grating install`
- `/ra 501 Stair 2 handrail from a 60t crane man box, hot works`
- `/email`, `/summarise`, `/ask what is hot on 501`, `/research <topic>`

How it works: a skill command (or any natural-language request with no active
job) goes to the brain. The brain runs the worker via Bash per
`reference/systems/agent-brain/vps-command-catalogue.md`, then marks the output
file with `[[SEND_FILE: <path>]]`; the bot turns that into a Telegram document
and strips the marker. While a `/skill` flow is open the bot keeps routing your
replies to the brain (so qualifying-question answers are not swallowed by
capture) until the brain emits `[[SKILL_DONE]]`. Safety documents come back as a
DRAFT for competent-person review.

VPS prerequisites for the command set: Chrome or Chromium installed (set
`CHROME_PATH` if non-standard); the full workspace present (scripts/, reference/
systems, assets/logos, templates, projects); `.env` keys `CLAUDE_CODE_OAUTH_TOKEN`
(brain), `ANTHROPIC_API_KEY` (workers), `MONDAY.COM_API_KEY` (toolbox/audit),
`SUPABASE_*` (ask-projects), `DEEPGRAM_API_KEY` + `NOTION_API_KEY` (capture).

## Run it 24/7 on the VPS (Phase 2)
Once proven on the desktop, deploy to the always-on VPS so it is reachable from your
phone anytime. Sketch (systemd unit):
```ini
[Unit]
Description=Dunsteel Operations Brain (Telegram)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/USER/dunsteel-workspace
EnvironmentFile=/home/USER/dunsteel-workspace/.env
ExecStart=/usr/bin/python3 scripts/agent/telegram_bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Prerequisites on the VPS: git clone of this workspace, Node + claude-code installed,
`CLAUDE_CODE_OAUTH_TOKEN` + bot env in `.env`, `pip install -r requirements.txt`.

## What it will and will not do
- **Freely:** read anything, run read-only scripts, write to Notion and `outputs/`,
  draft emails/documents (saved, not sent), update briefs and plans.
- **Only after you say yes in chat:** send anything outside Dunsteel, run the email
  router `--live` into Notion at scale, change live S: drive files, push to git, or
  commit money. These guardrails live in `system_prompt.md`.

## Files
- `agent_core.py` - the brain (wraps `claude -p`, subscription auth, Opus, tools).
- `telegram_bot.py` - the Telegram interface (allowlist, per-chat memory).
- `system_prompt.md` - the operating rules (tone, what is free vs confirm-first).
- `smoke_test.py` - prove auth + brain + memory + tool use.
- `requirements.txt` - just `python-telegram-bot`.

## Security notes
- The OAuth token and bot token live only in `.env` (gitignored). Never commit them.
- Single-user by allowlist. The brain acts as you, so keep the allowlist to your own
  chat id while trust is built. Desktop-first is the safe way to start.
