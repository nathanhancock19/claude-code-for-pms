# /onboard-pm — One-Command PM Workspace Installer

Stand up a Project Manager on their own Dunsteel workspace instance, end to end,
from a fresh clone/zip. This command is the conductor: it does everything that can
be automated, and for the few steps that cannot (creating a Telegram bot, making a
Notion teamspace, reading a chat id) it becomes a precise step-by-step guide that
tells the PM exactly what to do, waits, takes what they paste back, verifies it,
and moves on. Manual steps are guided checkpoints, not gaps.

Run by Nathan (operator), present on the PM's machine. Secrets are operator-seeded:
the shared Dunsteel keys come from Nathan's `.env`, never from the zip. Nathan's
own live instance is never touched.

## Variables

pm: $ARGUMENTS — the PM's canonical key (jack / james / john). If blank, ask which PM.

---

## Operating rules for the conductor (read first)

- **This runs IN the PM's own clone.** Before anything, confirm this is the PM's machine and NOT Nathan's live workspace. If `git remote -v` or `context/personal-info.md` suggests this is Nathan's primary setup, STOP and confirm with the operator.
- **Idempotent.** Keep progress in `outputs/internal/onboarding-status.md` (create on first run). At each phase, skip what is already marked done. A re-run resumes, never clobbers existing keys or DBs.
- **Secrets discipline.** Only ever write secrets to `.env` (gitignored). Never echo a full secret back, never put one in a committed file, never accept a secret via any channel but this local command. Confirm secrets by their first few characters only.
- **Parallelise the wait.** While the PM is doing a manual step (BotFather, Notion teamspace), run the automatable work concurrently with subagents (project derivation, context generation, reading Notion schema templates). Do not block on a human when there is machine work to do.
- **Verify every step before advancing.** A phase is "done" only when its check passes.
- All workers live under `scripts/provision/`. Deep reference: `clients/dunsteel/implementation/pm-rollout/provision-pm-runbook.md`. Plan: `plans/2026-06-21-telegram-aios-pm-fleet-rollout.md`.

---

## Phase 0 — Confirm + preconditions

1. Resolve the PM key from $ARGUMENTS (or ask). Confirm: "Setting up <PM>'s workspace on this machine. This is <PM>'s computer, not Nathan's primary setup, correct?"
2. Create/read `outputs/internal/onboarding-status.md` (phases 0-8, each Pending/Done + timestamp).
3. Quick machine check (from `SETUP.md` section 6): `python --version`, `node --version`, `git --version`, Claude Code signed into the shared Dunsteel account, Chrome present (for PDFs). Report any gaps and fix before continuing.
4. Mark Phase 0 done.

## Phase 1 — Seed the shared secrets (operator) + authorise Claude

The shared Dunsteel keys never travel in the zip; Nathan seeds them here.

1. Ask Nathan for the path to the shared Dunsteel `.env` on this machine (USB / private folder), e.g. `--shared-env`. Do NOT print its contents.
2. **Authorise Claude for the headless bot (guided, one-click).** The VPS bot has no browser to log in with, so it needs a token. Confirm Claude Code is signed into the **shared Dunsteel** account, then tell the PM/Nathan:
   > Run `claude setup-token`, click **Authorize** in the browser it opens, and paste the token back here.
   Write it into the shared `.env` (and it will flow to the PM `.env` in Phase 5) as `CLAUDE_CODE_OAUTH_TOKEN=...`. Confirm by prefix only. If a shared token already exists, reuse it and skip this.
3. Note that `GOOGLE_SERVICE_ACCOUNT_KEY` is multi-line inline JSON and must be copied by hand from the shared `.env` into the PM `.env` (build_pm_env flags it as MANUAL, leaves it blank).
4. Mark Phase 1 done (the `.env` itself is written in Phase 5).

## Phase 2 — Generate their workspace (automatable; run now, in parallel with Phase 3)

Kick this off and let it run while the PM starts Phase 3.

1. Dry-run then apply the workspace generator:
   ```
   python scripts/provision/generate_pm_workspace.py --pm <pm> --dest .
   python scripts/provision/generate_pm_workspace.py --pm <pm> --dest . --apply
   ```
   This derives the PM's active projects from the Day Docket Airtable, writes `context/*`, and trims `reference/projects/` to their jobs.
2. Show the PM their derived project list and let them correct it (re-run with `--projects 504,505` if needed).
3. Mark Phase 2 done.

## Phase 3 — Create their Telegram bot (guided manual)

Tell the PM, and wait:

> 1. Open Telegram, search for **@BotFather**, start a chat.
> 2. Send `/newbot`.
> 3. Name it `Dunsteel <PM> Ops` and give it a username ending in `bot`.
> 4. BotFather replies with a token like `8123456789:AAH...`. Paste that whole token back here.

When they paste it: validate the shape (`digits:alphanumerics`). Store it in memory for Phase 5; confirm only the first 6 chars. Mark Phase 3 done.

## Phase 4 — Create their Notion databases (guided manual + automatable)

1. Guided: ask the PM (or Nathan) to create the PM's **private Notion teamspace or a parent page** in the shared Dunsteel workspace, then connect the shared Dunsteel integration to it ("..." → Connections → the Dunsteel integration). Paste the parent page id back.
2. Automatable: create the DB set (General Notes + per-project Subcontractors Diary + Voice Memo Log), cloning Nathan's schemas:
   ```
   python scripts/provision/provision_pm_notion.py --pm <pm> --parent <page_id>
   python scripts/provision/provision_pm_notion.py --pm <pm> --parent <page_id> --apply
   ```
3. Copy the generated `scripts/provision/<pm>-site-diary-db-registry.local.json` to `outputs/internal/site-diary-db-registry.json`.
4. Mark Phase 4 done.

## Phase 5 — Write their .env (automatable)

```
python scripts/provision/build_pm_env.py --pm <pm> --dest . \
    --bot-token <token from Phase 3> --chat-id PENDING --shared-env <Phase 1 path>
python scripts/provision/build_pm_env.py --pm <pm> --dest . \
    --bot-token <token from Phase 3> --chat-id PENDING --shared-env <Phase 1 path> --apply
```
Shared keys copy from the seeded `.env`; per-PM keys (bot token, PM name) fill in. `chat-id` stays PENDING until Phase 7. Local AIOS keys stay blank (the PM adds those later via `/dunsteel-aios-setup`). Mark Phase 5 done.

## Phase 6 — Deploy the always-on bot on the VPS (operator step, guided)

The bot must run on the VPS so phone capture works when the PM's laptop is closed. This is Nathan's step (the VPS holds the deploy access).

1. Generate the unit + commands (dry-run shows exactly what to run):
   ```
   python scripts/provision/deploy_pm_bot.py --pm <pm> --workspace /opt/dunsteel/<pm>
   ```
2. Instruct Nathan: clone the PM's workspace to `/opt/dunsteel/<pm>` on the VPS (`root@your-vps.example.com`), copy the PM's `.env` there, then run with `--apply` under sudo. The runbook has the full sequence.
3. Confirm Nathan's existing bot is untouched: `systemctl is-active dunsteel-brain.service` (his live unit; the new per-PM unit is `dunsteel-bot-<pm>`).
4. Mark Phase 6 done once `dunsteel-bot-<pm>.service` is active.

## Phase 7 — Capture the chat id (guided manual)

Now the bot is live. Tell the PM, and wait:

> Open Telegram, find your new bot, send it `/whoami`. It replies with your chat id (a number). Paste it back here.

Patch the PM's `.env`: set `AGENT_ALLOWED_CHAT_IDS=<chat_id>` (re-run `build_pm_env.py` with `--chat-id <id> --apply`, or edit the line). Ask Nathan to restart the VPS unit: `sudo systemctl restart dunsteel-bot-<pm>.service`. Mark Phase 7 done.

## Phase 8 — Smoke test + daily brief + sign-off

1. From the clone: `python scripts/agent/verify_registry.py` — every one of the PM's DBs must come back OK.
2. PM sends a **voice note** to the bot → confirm it lands in THEIR Notion (General Notes / Voice Memo Log), stamped with their name.
3. PM runs a command from chat (e.g. `/toolbox`) → a PDF comes back.
4. Wire the daily brief: add the PM to `scripts/provision/pm_instances.json` (copy from `pm_instances.example.json`), then:
   ```
   python scripts/pm_daily_brief.py --pm <pm>            # dry-run
   python scripts/pm_daily_brief.py --pm <pm> --apply    # one live send to confirm
   ```
   Add a staggered AEST cron on the VPS (e.g. 06:00 / 06:05 / 06:10) so no two briefs run at once.
5. Confirm Nathan's instance is still running and untouched.
6. Mark Phase 8 done. Update `outputs/internal/onboarding-status.md` to complete.

## Done

Tell the PM what they now have (phone capture, daily brief, full command set) and point them to `/dunsteel-aios-setup` to connect their own optional tools (Monday, Sheets, Outlook) on their own machine later. Remind: their secrets live only in their local `.env`; never paste a secret into the Telegram bot.

---

### Notes for Claude

- Never write a secret to anything but `.env`. Confirm secrets by prefix only.
- If a worker errors, stop at that phase, show the error, fix, and re-run that phase only. Do not advance on a failed check.
- Use subagents to run Phase 2 (and Notion schema reads for Phase 4) while the PM is occupied in Phase 3, so the human wait is never idle machine time.
- Everything is scoped to the current clone; the provision scripts refuse to write into Nathan's repo by design.
