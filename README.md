# Claude Code for Project Managers

How I run a real construction project-management job with [Claude Code](https://claude.com/claude-code) as the engine.

I'm Nathan, a project coordinator in structural steel. This repo is the working setup I use to take the repetitive admin off my plate: drafting safety documents, capturing the site diary by voice, classifying email, searching the file server, and running it all from my phone through a Telegram bot. It's the "how", with the company's real project and client data stripped out and replaced with fictional placeholders.

> Built for a YouTube series on using Claude Code as a PM. The code is real; the example projects, clients, and names are invented.

---

## The idea

A PM's day is mostly the same handful of admin tasks repeated across projects: SWMS and risk assessments, toolbox talks, safety audits, variations, bolt certs, diary entries, chasing email, finding the right file. Each one is low-judgement but time-consuming.

The bet here is **assisted systems, not full automation**: Claude does the drafting, searching, and summarising; the human keeps every decision. The result is a set of [slash commands](.claude/commands/) and worker scripts that turn a one-line instruction into a finished document, plus an always-on phone interface so it works from a job site.

## What's in here

```
.claude/commands/   slash commands you run inside Claude Code
scripts/            the worker scripts each command drives
scripts/agent/      the Telegram bot "brain" (run your PM workflow from your phone)
scripts/provision/  one-command onboarding to give another PM their own instance
reference/          the bot's command catalogue
plans/              design docs for the phone bot + multi-PM rollout
CLAUDE.md           the operating manual Claude loads at the start of every session
```

## The commands

Run these inside a Claude Code session. Each one is a Markdown instruction file in [`.claude/commands/`](.claude/commands/) backed by a Python worker in [`scripts/`](scripts/).

### Safety & compliance documents
| Command | What it produces |
|---|---|
| `/swms 501 "crane lift and boom-lift operations"` | A signed-ready Safe Work Method Statement (HTML -> PDF), hazards/controls/PPE tailored by Claude |
| `/risk-assessment 501 "stair handrail install from a crane man-box, hot works"` | A full risk assessment with hazard matrix and controls |
| `/safety-audit 501 --scope "L6 grating install"` | A pre-start safety audit PDF + an item on the Monday safety board |
| `/toolbox-talk 501` | The next toolbox talk in a rolling program + a Monday board item |
| `/bolt-cert 501 "Level 2"` | Bolt tightening certificate + method statement (DOCX + PDF) into the QA folder |

> Generated safety documents are **drafts for a competent person to review** before issue.

### Commercial & project documents
| Command | What it produces |
|---|---|
| `/dunsteel-variation 501 "stair access modifications"` | Auto-numbers the next variation folder, pre-fills the Excel template, opens it |
| `/dunsteel-variation-pack 1 2 3` | Compiles supporting docs for a set of variations and drafts a cover email |
| `/dunsteel-project-review 501` | A post-project review slide deck (PowerPoint) from a questionnaire |

### Find things & set up projects
| Command | What it does |
|---|---|
| `/find-on-sdrive 501 "latest delivery schedule"` | Natural-language search of the file server, ranked by relevance and recency |
| `/new-project 501` | Crawls the file server, scrapes Outlook, creates the Notion page, and writes a project brief — one command to onboard a new job |

### Daily PM workflow
| Command | What it does |
|---|---|
| `/dunsteel-brief` | Morning brief: deliveries, ordering alerts, outstanding actions |
| `/dunsteel-email "chase the head contractor on the Level 3 ETA"` | Drafts an email in your voice |
| `/dunsteel-summarise <file>` | Summarises a contract, spec, or long document |
| `/ask-projects "what variations are unresolved"` | Answers questions over a live project event stream |

## The phone bot (`scripts/agent/`)

The part that makes this work in the field. An always-on Telegram bot, running on a small VPS, that puts the whole toolkit in your pocket.

- **Text a command, get a PDF back.** `/swms`, `/ra`, `/toolbox`, `/audit`, `/email`, `/summarise`, `/ask`, `/research` all run from chat. The bot asks any clarifying questions, then the finished PDF lands in the conversation.
- **Voice the site diary.** Narrate the day as you go ("two on Stair 2 doing handrails, a rigging crew on the canopies"). Notes buffer through the day; at 5pm a cron job builds one structured diary entry per project in Notion and pulls out any tasks.
- **Voice straight to notes.** With a project active, any voice memo is transcribed (Deepgram), structured by Claude, and logged to Notion in seconds.

Architecture: Telegram -> `telegram_bot.py` -> `agent_core.py` (runs `claude -p` headless on your Claude Max plan) -> worker scripts -> PDF back to chat. See [`scripts/agent/README.md`](scripts/agent/README.md) and the [command catalogue](reference/systems/agent-brain/vps-command-catalogue.md).

## Rolling it out to a team (`scripts/provision/`)

The setup is built as a **per-PM template**. `/onboard-pm` is a one-command installer that gives another PM their own isolated instance — their own workspace, Telegram bot, Notion databases, and daily brief — without touching anyone else's. See [the rollout plan](plans/2026-06-21-telegram-aios-pm-fleet-rollout.md).

## Getting started

1. Install [Claude Code](https://claude.com/claude-code) and clone this repo.
2. `cp .env.example .env` and fill in the keys for the services you want to use. Everything reads from `.env`; nothing is hardcoded.
3. Open the folder in Claude Code — `CLAUDE.md` loads automatically and `/prime` orients the session.
4. Try a command: `/swms 501 "working at heights on a structural steel frame"`.
5. For the phone bot: create a bot with [@BotFather](https://t.me/botfather), set `AGENT_BOT_TOKEN` and `AGENT_ALLOWED_CHAT_IDS`, then run `python scripts/agent/telegram_bot.py`.

## Notes

- **Python** for the workers (PDF generation via headless Chrome, API calls). **Claude Code** as the reasoning engine. **n8n** runs some always-on automations not included here.
- Worker scripts default to your Claude Max subscription via an OAuth token, falling back to a metered API key.
- This is my real working setup shared as a reference, not a packaged product. Adapt it to your own systems.

## License

[MIT](LICENSE) © 2026 Nathan Hancock
