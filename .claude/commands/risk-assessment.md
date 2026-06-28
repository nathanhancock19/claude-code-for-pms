# Risk Assessment

> Generate a Dunsteel Risk Assessment (HTML + PDF) from a project number and a free-text activity / method description. Sister command to `/swms`, using the same project lookup and PDF pipeline.

## Variables

project: first argument (project number, e.g. `501`)
activity: remaining arguments (free-text activity / scope / proposed method)

## Usage

```
/risk-assessment 501 "Stair 2 handrail and SHS install from a 60t crane man box, hot works, no tag lines"
/risk-assessment 501 "Bolt tightening to Level 7 connections from EWP"
/risk-assessment 504 "Cooling tower stair grating install, EWP, work at height"
```

First argument is the project number. Everything after is the activity description. Include the proposed method in the description (e.g. "rigged to man box, no tag lines") so the controls match how the job is actually done.

---

## What This Does

Thin wrapper around `scripts/risk_assessment.py`, which reuses the `/swms` plumbing (`scripts/swms.py`) for project lookup, `.env` loading, PDF conversion and slugging, and adds a Risk-Assessment-specific schema, Claude prompt, HTML renderer and offline fallback.

Pipeline:

1. **Project lookup** - finds the S: drive folder matching `[number] -*` under `S:\Operations\01 Current Project\` and parses `[number] - [head contractor] - [project name]`. Project 501 gets its known Riverside / Northbridge Constructions context applied.
2. **Payload generation** - calls Claude (`claude-sonnet-4-6`) with a structured prompt to produce an RA payload: `ra_name`, `subtitle`, `task_description`, `supporting_docs`, `hazardous_materials`, `consultation`, `hrcw` (8 standard categories), `qualifications`, `plant`, `ppe`, `permits`, `activities` (rows of HRCW category / step / hazard / initial risk / **controls as a list** / residual risk), `footer_note`. Controls are returned as a list so each control renders on its own line.
3. **HTML render** - renders the payload to the Dunsteel RA house style: white header with the Dunsteel logo top-right (embedded as base64 so it shows regardless of save location), project details, task description, supporting docs, hazardous materials, consultation, high risk work checklist, qualifications / plant / PPE / permits, fixed risk matrix + key, per-activity hazard-control tables with bulleted controls and narrow initial/revised risk columns, and a blank worker sign-off table.
4. **PDF conversion** - via `scripts/html_to_pdf.py` (Chrome headless).
5. **Save** - writes both files to the project's `15 WHS and Environmental` folder by default.

---

## Instructions

### Step 1: Parse arguments

- `project` = first token of `$ARGUMENTS`.
- `activity` = the rest (free-text method / scope).

If no activity description was given, ask Nathan what the activity and proposed method are before running. Method detail matters (e.g. whether tag lines are used, whether a man box or EWP is used, whether there are hot works) - it changes the controls.

### Step 2: Run the generator

Default (saves to the project's `15 WHS and Environmental` folder on the S: drive):

```bash
python scripts/risk_assessment.py <project> "<activity>"
```

Override the save location:

```bash
python scripts/risk_assessment.py <project> "<activity>" --out-dir "C:/some/dir"
```

Verification pass that never touches the S: drive (writes to `outputs/automation/risk-assessments/TEST_RUN/`):

```bash
python scripts/risk_assessment.py <project> "<activity>" --test
```

### Step 3: Report

Report back to Nathan:
- The HTML and PDF paths.
- Where it saved (project WHS folder, override, or workspace fallback).
- Whether the payload came from Claude or the built-in fallback.
- That it is a DRAFT requiring competent-person review before issue.

---

## Save Location

| Situation | Save location |
|---|---|
| Default | `[project]\15 WHS and Environmental\` on the S: drive |
| `--out-dir DIR` given | the directory you specify |
| `--test` given | `outputs/automation/risk-assessments/TEST_RUN/` (S: drive never touched) |
| WHS folder not found and no override | `outputs/automation/risk-assessments/` (workspace fallback) |

Output filename: `RA-[number]-[ra-name-slug].html` / `.pdf`.

---

## API Key Note

`scripts/risk_assessment.py` calls `claude-sonnet-4-6` when `ANTHROPIC_API_KEY` is set (it self-loads the workspace `.env`). If the key is not set, it falls back to a built-in deterministic structural-steel-at-height RA so the render and PDF pipeline still runs, and reports that the fallback was used. For real RAs, set `ANTHROPIC_API_KEY` so Claude tailors the hazards and controls.

---

## Critical Rules

- **No long dashes** anywhere in the command, the script, or the generated RA. The script also strips any that slip through. Use a hyphen, a colon, or restructure.
- **Match the stated method.** Do not add controls that contradict how the job is actually done (for example, do not add tag lines if the method does not use them). Put the method in the activity description.
- **Human review required.** A generated RA is a draft. It must be reviewed, corrected, and signed off by a competent person before it is issued or relied on for work.
- **Do not guess a live save path.** If the WHS folder cannot be found and no `--out-dir` is given, the script saves to the workspace and reports it rather than writing into an unexpected live location.

---

## Reuse Note

This command reuses the worked Stair 2 RA template (`outputs/501-riverside/risk-assessment-stair2-handrail-shs-manbox.html`) as the house style and shares the `/swms` project-lookup and PDF plumbing. The risk matrix and hierarchy of controls are fixed in the renderer; Claude fills the variable content from free text.
