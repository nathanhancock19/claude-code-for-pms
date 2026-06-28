# Safety Audit

> Generate a Dunsteel-branded pre-start safety audit for a project, create the matching item on the Monday "Safety Audit" board, and render a PDF locally. Surface 1 of the PM Safety + Compliance Automation. (Nathan refers to this as the "safety order" by voice.)

## Variables

project: first argument (project number, e.g. `501`)
scope: `--scope "..."` the day's scope of work (drives Tasks/Hazards/Controls)

## Usage

```
/safety-audit 501 --scope "L6 grating install"
/safety-audit 501 --scope "Bolt-up L7" --fail "ppe:no dust mask"
/safety-audit 501 --scope "Stair install" --ai
```

First argument is the project number. `--scope` is the day's work.

---

## What This Does

Thin wrapper around `scripts/safety_audit.py`:

1. **Project lookup** - resolves the project number to name + head contractor (project brief, with fallback).
2. **Content** - Tasks/Hazards/Controls come from `--tasks/--hazards/--controls` if given, else from Claude when `--ai` is set (and `ANTHROPIC_API_KEY` is present), else a sensible structural-steel default keyed off `--scope`.
3. **Checks** - the seven pre-start checks default to Yes. Mark one failed with `--fail "key:reason"` (keys: qual, permit, logbook, ppe, electrical, materials, leadhooks); repeatable.
4. **Monday item** - creates an entry on board `YOUR_SAFETY_AUDIT_BOARD_ID` with Date, Job, PM, check statuses, Tasks/Hazards/Controls, then sets `Automation Status = PDF Created`.
5. **PDF render** - fills `reference/systems/toolbox-talks/safety-audit-template.html` and converts via `scripts/html_to_pdf.py`.
6. **Save** - writes HTML + PDF to `outputs/[project]/safety-audits/`.

Board/column IDs live in `reference/systems/toolbox-talks/monday-board-map.md`.

---

## Instructions

### Step 1: Parse arguments
- `project` = first token. Pass `--scope`, `--fail`, `--ai`, `--date` through. If no scope is given, ask Nathan what the day's work is before running (it drives the hazards/controls).

### Step 2: Run the generator

```bash
python scripts/safety_audit.py <project> --scope "<scope>"
```

Useful flags:
- `--dry-run` - HTML only, no PDF, no Monday write.
- `--no-monday` - HTML + PDF, no board write.
- `--test` - write to `outputs/automation/safety-audits/TEST_RUN/`.
- `--ai` - draft Tasks/Hazards/Controls with Claude.
- `--pm "Name"` - default Nathan Hancock.

### Step 3: Report
- The HTML + PDF paths, any failed checks, and the Monday item URL.

---

## Save Location

| Situation | Save location |
|---|---|
| Default | `outputs/[project]/safety-audits/` |
| `--out-dir DIR` | the directory you specify |
| `--test` | `outputs/automation/safety-audits/TEST_RUN/` (board never touched) |

Output filename: `[project]-safety-audit-[YYYY-MM-DD]-[scope-slug].html` / `.pdf`.

---

## Critical Rules

- **No long dashes** anywhere in the script, this command, or generated content.
- **Human review + sign-off before work.** A pre-start audit is the supervisor's confirmation the area is safe. The generated draft must be reviewed, the checks confirmed against the real site, and signed before work proceeds. Default "Yes" answers are a starting point, not a substitute for the walk-around.
- The Monday token is read from `.env` (`MONDAY.COM_API_KEY`) via `scripts/dunsteel_monday.py`; never hardcode it.
