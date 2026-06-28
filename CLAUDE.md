# CLAUDE.md

This file is loaded automatically at the start of every Claude Code session in this workspace. It tells Claude what this is and how to operate here.

---

## What This Is

This is a **Project Manager's Claude Code workspace** — the setup Nathan (a project coordinator in structural steel) uses to take repetitive admin off his plate: safety documents, variations, site diaries, email, file search, and reporting. Claude is the reasoning engine; Python worker scripts do the rendering and API calls; a Telegram bot puts it all on the phone.

**Philosophy:** assisted systems, not full automation. Claude handles the drafting, searching, and summarising. The human keeps every decision. Generated safety documents are always drafts for a competent person to review before issue.

---

## The Claude-User Relationship

- **Nathan** sets direction, provides context, and approves work through commands.
- **Claude** reads context, executes commands, produces outputs, and keeps the workspace consistent.

Orient at session start with `/prime`, then act with awareness of the active projects and priorities.

---

## Workspace Structure

```
.
├── CLAUDE.md            # This file — core context, always loaded
├── .claude/commands/    # Slash commands Claude can execute
├── scripts/             # Worker scripts behind the commands
│   ├── agent/           # The Telegram bot "brain"
│   └── provision/       # One-command onboarding for additional PMs
├── reference/           # System docs (e.g. the bot command catalogue)
└── plans/               # Design/implementation docs
```

In a live instance there are also `context/` (background on the PM and active projects), `outputs/` (deliverables, organised by project), and `reference/projects/` (per-project briefs). Those hold real data and are not part of this public template.

---

## Commands

### Session
| Command | Purpose |
|---|---|
| `/prime` | Load context and summarise workspace state at session start |
| `/daily-brief` | Daily priorities and status |
| `/weekly-review` | Weekly review and next-week planning |

### PM daily tools
| Command | Purpose |
|---|---|
| `/dunsteel-brief` | Morning PM brief — projects, deliveries, ordering alerts |
| `/dunsteel-email [context]` | Draft an email in Nathan's voice |
| `/dunsteel-summarise [file]` | Summarise contracts, specs, long documents |
| `/ask-projects [question]` | Answer plain-language questions over the live project event stream |

### Document production
| Command | Purpose |
|---|---|
| `/swms [number] "[activity]"` | Draft a Safe Work Method Statement (HTML -> PDF) |
| `/risk-assessment [number] "[activity]"` | Draft a Risk Assessment with hazard matrix |
| `/safety-audit [number] --scope "..."` | Pre-start safety audit PDF + Monday board item |
| `/toolbox-talk [number]` | Weekly toolbox talk from a rolling program + Monday item |
| `/bolt-cert [number] [scope]` | Bolt tightening certificate + method (DOCX + PDF) |
| `/dunsteel-variation [number] "[title]"` | Create a new variation from the Excel template |
| `/dunsteel-variation-pack [numbers]` | Compile variation supporting docs + cover email |
| `/dunsteel-project-review [number]` | Post-project review slide deck (PowerPoint) |

### Project setup & search
| Command | Purpose |
|---|---|
| `/new-project [number]` | Onboard a new project: crawl the file server, scrape Outlook, create the Notion page, write a brief |
| `/find-on-sdrive [number?] [query]` | Natural-language search of the file server |

### Fleet
| Command | Purpose |
|---|---|
| `/onboard-pm [pm]` | One-command installer to give another PM their own isolated instance (workspace, bot, Notion DBs, daily brief) |

### Maintenance
| Command | Purpose |
|---|---|
| `/commit [message]` | Save work and keep docs current |
| `/update-docs` | Keep documentation current after changes |
| `/update-status` | Append a dated checkpoint to a plan file |
| `/capture [idea]` | Quick-capture an idea or note |
| `/task-audit` | Map recurring tasks to an automation maturity model |

---

## Maintain This File

When a change adds functionality, modifies structure, or introduces a command, update the relevant section here. CLAUDE.md is the single source of truth for how Claude operates in this workspace.

---

## Formatting Rules — Non-Negotiable

- **No em dashes (—) anywhere.** Not in reports, emails, HTML documents, Markdown, or any other output. Use a plain hyphen, a colon, or restructure the sentence. Hard rule, no exceptions.

---

## Notes

- Secrets live in `.env` (gitignored). Nothing is hardcoded; every script reads its keys from the environment. See `.env.example`.
- Worker scripts default to a Claude Max subscription via an OAuth token, falling back to a metered API key.
- The Telegram bot runs a subset of these commands from the phone (SWMS, risk assessment, toolbox talk, safety audit, email, summarise, ask-projects, research) and returns the PDF to chat. File-server and Office commands (variation, bolt cert, find-on-sdrive, new-project) are PC-only. Scope: `reference/systems/agent-brain/vps-command-catalogue.md`.
- This workspace is also a per-PM template: each PM gets their own clone, `.env`, Notion databases, and always-on bot, provisioned by `/onboard-pm`.
