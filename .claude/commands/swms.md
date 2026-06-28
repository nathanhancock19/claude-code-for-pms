# SWMS

> Generate a Dunsteel Safe Work Method Statement (HTML + PDF) from a project number and a free-text activity description. Wrapper around the existing SWMS generator pattern.

## Variables

project: first argument (project number, e.g. `501`)
activity: remaining arguments (free-text activity / scope description)

## Usage

```
/swms 501 "Stair 6 reinstatement - hot works + EWP + working at heights"
/swms 501 "Bolt tightening on Level 7"
/swms 504 "Cooling tower stair grating install"
```

First argument is the project number. Everything after is the activity description.

---

## What This Does

This is a thin wrapper around the existing Dunsteel SWMS generator pattern (`scripts/generate_riverside_swms*.py`, `scripts/generate_rigging_swms_payload.py`). It standardises the entry point so a SWMS can be produced from one line instead of hand-editing a payload script.

Pipeline (all handled by `scripts/swms.py`):

1. **Project lookup** - finds the S: drive folder matching `[number] -*` under `S:\Operations\01 Current Project\` and parses `[number] - [head contractor] - [project name]` from the folder name. Project 501 also gets its known Riverside / Northbridge Constructions / Stratus SYD2 context applied (no 501 brief file exists yet).
2. **Payload generation** - calls Claude (`claude-sonnet-4-6`) with a structured prompt to produce a SWMS payload that conforms to the exact schema used by the existing generators: `hrcw`, `qualifications`, `plant`, `ppe`, `permits`, `activities` (rows of HRCW category / step / hazard / initial risk / controls / residual risk), `consultation`, `revisions`, `footer_note`.
3. **HTML render** - renders the payload to HTML matching the live Dunsteel SWMS document quality (hazard/control tables, HRA checklist, PPE, plant, permits, qualifications, consultation, revision history, sign-off table).
4. **PDF conversion** - converts the HTML to PDF via `scripts/html_to_pdf.py` (Chrome headless - the mandated HTML to PDF path).
5. **Save** - writes both files to the project's `15 WHS and Environmental` folder by default.

---

## Instructions

Run the worker script and report the result.

### Step 1: Parse arguments

- `project` = the first token of `$ARGUMENTS`.
- `activity` = the rest of `$ARGUMENTS` (the free-text description).

If no activity description was given, ask Nathan what the activity is before running.

### Step 2: Run the generator

Default (saves to the project's `15 WHS and Environmental` folder on the S: drive):

```bash
python scripts/swms.py <project> "<activity>"
```

To override the save location:

```bash
python scripts/swms.py <project> "<activity>" --out-dir "C:/some/dir"
```

To run a verification pass that never touches the S: drive (writes to `outputs/automation/swms/TEST_RUN/`):

```bash
python scripts/swms.py <project> "<activity>" --test
```

### Step 3: Report

Report back to Nathan:
- The HTML and PDF paths.
- Where it saved (project WHS folder, override, or workspace fallback).
- Whether the payload came from Claude or the built-in fallback (see note below).

---

## Save Location

| Situation | Save location |
|---|---|
| Default | `[project]\15 WHS and Environmental\` on the S: drive |
| `--out-dir DIR` given | the directory you specify |
| `--test` given | `outputs/automation/swms/TEST_RUN/` (S: drive never touched) |
| WHS folder not found and no override | `outputs/automation/swms/` (workspace fallback - the skill will not guess a live S: drive path) |

Output filename: `SWMS-[number]-[activity-slug]-Rev[n].html` / `.pdf`.

---

## API Key Note

`scripts/swms.py` calls `claude-sonnet-4-6` when `ANTHROPIC_API_KEY` is set in the environment (standard Anthropic SDK behaviour). If the key is not set, the script falls back to a built-in deterministic SWMS payload so the render and PDF pipeline still runs, and it reports clearly that the fallback was used. For real SWMS documents, set `ANTHROPIC_API_KEY` so Claude tailors the hazards and controls to the specific activity. The fallback is a generic structural-steel-at-heights template and should always be reviewed and edited before issue.

---

## Reuse Note

The payload **schema** is the shared contract reused verbatim from the existing generators - this skill does not rebuild the generator. Claude fills that schema from free text instead of it being hand-coded per job. PDF rendering uses the same `scripts/html_to_pdf.py` (Chrome headless) path as every other live Dunsteel document.

---

## Critical Rules

- **No long dashes** anywhere in the skill, the script, or the generated SWMS content. Use a hyphen, a colon, or restructure.
- **Human review required.** A generated SWMS is a draft. It must be reviewed, corrected, and signed off by a competent person before it is issued or relied on for work.
- **Do not guess a live save path.** If the WHS folder cannot be found and no `--out-dir` is given, the skill saves to the workspace and reports it rather than writing into an unexpected live location.
