# VPS Command Catalogue (Operations Brain)

> Single source of truth for which workspace commands the Telegram brain may run
> on the **VPS**, the exact worker invocation, where the output lands, and the
> rules that apply. The brain reads this (via `scripts/agent/system_prompt.md`)
> before running a command from chat. Last updated 2026-06-05.

## How the brain runs a command

1. Ask the qualifying questions first for any safety document (method, crew,
   height, plant, permits) before generating. Keep it tight.
2. Run the worker with `Bash`, using the exact flags below. On the VPS there is
   **no S: drive**, so SWMS/RA save into the project's `outputs/<id>-.../` folder
   automatically (no flag needed); pass `--out-dir` only to override.
3. When a deliverable file is produced, emit a marker on its own line so the bot
   sends it to Nathan's phone:
   `[[SEND_FILE: <absolute path to the PDF>]]`
4. When the task is finished, emit `[[SKILL_DONE]]` on its own line so the bot
   returns the chat to normal routing.
5. Safety documents are always **DRAFT - competent-person review before issue**.
   Say so in the reply.

## In scope (runs on the VPS)

| Command | Worker invocation | Output | Notes |
|---|---|---|---|
| SWMS | `python scripts/swms.py <id> "<activity>"` | `outputs/<id>-.../swms/SWMS-*.pdf` | Claude-tailored (`ANTHROPIC_API_KEY`). DRAFT. Ask qualifying questions first. |
| Risk Assessment | `python scripts/risk_assessment.py <id> "<activity + method>"` | `outputs/<id>-.../risk-assessments/RA-*.pdf` | Put the actual method in the activity text so controls match. DRAFT. |
| Toolbox Talk | `python scripts/toolbox_talk.py <id> [--topic "<topic>"]` | `outputs/<id>-.../toolbox-talks/*.pdf` | Creates the Monday Toolbox Talk item (board YOUR_TOOLBOX_BOARD_ID) + PDF. `--no-monday` for PDF only. No API key needed (curated topics). |
| Safety Audit | `python scripts/safety_audit.py <id> --scope "<scope>"` | `outputs/<id>-.../safety-audits/*.pdf` | Creates the Monday Safety Audit item (board YOUR_SAFETY_AUDIT_BOARD_ID) + PDF. `--fail "ppe:reason"` to mark a failed check. DRAFT - supervisor sign-off before work. |
| Email draft | `/dunsteel-email` worker / draft to file | `outputs/<id>-.../emails/` | DRAFT only. Never send externally - Nathan sends. Match his voice (casual, direct, inline thanks, no sign-off). |
| Summarise | `/dunsteel-summarise` (paste or file) | chat reply | Contracts, specs, long docs. |
| Ask projects | `python scripts/project_events_query.py "<question>"` | chat reply | Reads the Supabase `project_events` stream. "what is hot on 501", "what variations are unresolved", "where am I waiting on a reply". |
| Research | `/deep-research` (web) | chat reply or `outputs/research/` | For genuine multi-source questions. |
| Capture | `/capture` | workspace | Quick idea/note capture. |

## Out of scope on the VPS (needs the S: drive or Microsoft Office)

Decline these with one line, e.g. "that one runs from the PC (needs the S:
drive / Word / Excel)" - do not attempt them on the VPS.

| Command | Why it cannot run on the VPS |
|---|---|
| `/dunsteel-variation` | PM08 **Excel** template on the S: drive (Excel COM). |
| `/bolt-cert` | Needs **Word** for PDF; writes to the S: QA folder. |
| `/find-on-sdrive` | Reads the **S: drive**. |
| `/new-project` | S: inventory + Outlook scrape. |
| `/dunsteel-variation-pack` | Reads S: drive files. |

When that work moves to a PC-hosted instance of the brain (or the workers are
made S:-optional), flip the row from this section to the in-scope table.

## Prerequisites on the VPS

- Chrome or Chromium installed (for `html_to_pdf.py`); set `CHROME_PATH` if not
  at a standard path.
- Full workspace present (scripts/, reference/systems/, reference/assets/logos/,
  reference/templates/, reference/projects/).
- `.env` keys: `CLAUDE_CODE_OAUTH_TOKEN` (brain), `ANTHROPIC_API_KEY` (workers),
  `MONDAY.COM_API_KEY` (toolbox/audit), `SUPABASE_*` (ask-projects),
  `DEEPGRAM_API_KEY` + `NOTION_API_KEY` (capture).
