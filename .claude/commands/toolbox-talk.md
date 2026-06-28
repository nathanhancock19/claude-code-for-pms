# Toolbox Talk

> Generate a Dunsteel-branded weekly toolbox talk for a project, create the matching item on the Monday "Toolbox Talk" board, and render a PDF locally. Surface 1 of the PM Safety + Compliance Automation.

## Variables

project: first argument (project number, e.g. `501`)
topic: optional `--topic` (number or name); default is the next topic in the weekly rotation

## Usage

```
/toolbox-talk 501
/toolbox-talk 501 --topic "silica"
/toolbox-talk 501 --topic 6 --date 2026-06-08
```

First argument is the project number. With no `--topic`, the next topic in the rolling weekly program is chosen by ISO week.

---

## What This Does

Thin wrapper around `scripts/toolbox_talk.py`:

1. **Project lookup** - resolves the project number to name, head contractor, and location from `reference/projects/[id]/project-brief.md` (with a built-in fallback for known projects).
2. **Topic selection** - explicit `--topic` (number or name) wins; otherwise rotates through `reference/systems/toolbox-talks/topic-program.json` (20 curated topics with points, hazards, controls) by week.
3. **Monday item** - creates an entry on board `YOUR_TOOLBOX_BOARD_ID` (group "Incoming responses") with Date, Job, Reason = Weekly, PM, Agenda, Hazards, Controls, and the four compliance checks ticked, then sets `Automation Status = PDF Created`.
4. **PDF render** - fills `reference/systems/toolbox-talks/toolbox-talk-template.html` (Dunsteel letterhead, attendance register, sign-off) and converts to PDF via `scripts/html_to_pdf.py` (Chrome headless).
5. **Save** - writes HTML + PDF to `outputs/[project]/toolbox-talks/`.

Board/column IDs live in `reference/systems/toolbox-talks/monday-board-map.md`.

---

## Instructions

Run the worker and report the result.

### Step 1: Parse arguments
- `project` = first token of `$ARGUMENTS`. Pass any `--topic`, `--date` through.

### Step 2: Run the generator

```bash
python scripts/toolbox_talk.py <project> [--topic "<topic>"] [--date YYYY-MM-DD]
```

Useful flags:
- `--dry-run` - render HTML only, no PDF, no Monday write.
- `--no-monday` - render HTML + PDF but do not touch the board.
- `--test` - write to `outputs/automation/toolbox-talks/TEST_RUN/`, never the board.
- `--list` - list the 20 topics and exit.
- `--conducted-by "Name"` / `--role "Title"` - default Nathan Hancock, Project Manager.
- `--attendees "Sam, Luke, Kieran, Jesse"` - pre-fill the register with these names. Overrides carry-forward. When supplied, the blank rows below are removed (use `--blank-rows N` to keep some).
- `--company "Apex Rigging"` - company applied to every pre-filled / added attendee.
- `--add "Jesse"` or `--add "Jesse:Apex Cranes"` - add a new starter on top of the carried-forward crew (repeatable).
- `--remove "Sam"` - drop a carried-forward attendee who is no longer on site (repeatable).
- `--no-carry` - start from a blank register instead of carrying last week forward.
- `--no-sign` - leave the signature cell blank instead of the attendee name in cursive script (default is to pre-fill the signature).
- `--blank-rows N` - number of empty register rows (default 14 with no attendees, 0 when attendees are given).

### Attendance carry-forward (automatic)

By default the script reads the **most recent prior talk for the same project** in `outputs/[project]/toolbox-talks/` and carries those attendees (name + company) forward. This is the normal weekly case: if nothing changed on site, just run `/toolbox-talk [project]` and the same crew is filled in. When the crew changes, use `--add` / `--remove`, or `--attendees` to set the list from scratch. The script prints which file it carried from.

### Monday attendees

When attendees are present, their names are written into the board's "Attendee N Name" columns, each suffixed `(signed)` to record that the crew signed the printed register (the PDF holds the actual signatures). The columns are discovered live by title, so the messy numbering on the board does not matter. Signature columns are file type and are **not** written via the API (Monday file upload via API does not work); the signed evidence is the PDF.

Hazards and controls render one item per line with spacing (no longer a single run-on paragraph).

### Step 3: Report
- The HTML + PDF paths, the topic used, and the Monday item URL.
- If the project is not in the board's Job dropdown, note that Job was left blank (the script prints this).

---

## Save Location

| Situation | Save location |
|---|---|
| Default | `outputs/[project]/toolbox-talks/` |
| `--out-dir DIR` | the directory you specify |
| `--test` | `outputs/automation/toolbox-talks/TEST_RUN/` (board never touched) |

Output filename: `[project]-toolbox-talk-[YYYY-MM-DD]-[slug].html` / `.pdf`.

---

## Critical Rules

- **No long dashes** anywhere in the script, this command, or generated content.
- **Human review + on-site sign-off.** The generated talk is a delivery draft. It must be run with the crew and signed on the attendance register before it counts as completed evidence.
- The Monday token is read from `.env` (`MONDAY.COM_API_KEY`) via `scripts/dunsteel_monday.py`; never hardcode it.
