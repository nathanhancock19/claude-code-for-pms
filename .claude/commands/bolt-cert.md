# Bolt Cert

> Generate a Dunsteel bolt tightening Certificate (PM17) AND Bolt Tightening Methods (PM16) as DOCX + PDF, filled with project + scope details, saved to the QA scope folder on the S: drive.

## Variables

args: $ARGUMENTS (project number + scope, e.g. `501 Level 2`)

---

## What This Does

For a given project and scope, this command produces **two separate documents, four files total**:

| Source template | Output DOCX | Output PDF |
|---|---|---|
| PM17. Bolt tightening Certificate.docx | `[number] - Bolt tightening Certificate - [scope].docx` | same name `.pdf` |
| PM16. Bolt Tighteneing Methods.docx | `[number] - Bolt Tightening Methods - [scope].docx` | same name `.pdf` |

Both are saved into `[project]\08 QA\[scope]\`. PM17 is the project-specific certificate (placeholders filled). PM16 is the standing Dunsteel bolt tightening method statement that ships with the cert (its only edit is the footer line, updated to the signatory + current month/year). Both are required by head contractors as a pair, so the command always generates both.

The Python worker `scripts/bolt_cert.py` does all the file work. This command is the interactive wrapper.

---

## Usage

```
/bolt-cert 501 Level 2
/bolt-cert 501 Stair 1
/bolt-cert 505 HV Room
/bolt-cert 501              <- scope omitted: lists available scopes and asks
```

- **First token** = project number.
- **Remaining tokens** = scope (QA sub-folder name): `Level 2`, `Stair 1`, `HV Room`, etc.

---

## Instructions

Follow this exactly.

### Step 1: Parse arguments

Split `$ARGUMENTS`. The first token is the project number. The rest is the scope string.

If **no scope** was given, run the worker in list mode to show what is available, then ask which one:

```
python scripts/bolt_cert.py --project [number] --list-scopes
```

Present the returned scope list and ask: "Which scope - [list]?" Wait for the answer before continuing. Do not guess.

### Step 2: Resolve project facts

The worker fills these placeholders. Defaults below are baked into the script; override only when the project differs.

- **Head contractor (Attn line):** read from the project brief at `reference/projects/[number]-[slug]/project-brief.md` if it exists. For **501** there is no brief yet: use **Northbridge Constructions**.
- **Project name:** for 501 use **Stratus Riverside**. Otherwise read from the brief.
- **Signatory (default):** **Nathan Hancock**, role **Project Manager**, phone **04XX XXX XXX**. If Nathan asks for a different signatory, pass `--signatory`, `--role`, `--phone`.
- **Date:** today (set automatically by the worker).
- **Scope reference:** the scope is appended to the project name on the certificate (e.g. "Stratus Riverside - Level 2"), matching the existing 501 cert convention.

### Step 3: Check the scope folder exists

The worker resolves `[project]\08 QA\[scope]`. If that folder does not exist, the worker errors out **without creating anything** (safe default).

When that happens, ask Nathan: **"Scope folder '[scope]' not found under 08 QA. Create it?"**

- If **yes**: re-run the worker with `--create-scope`.
- If **no**: stop and report the available scopes (run `--list-scopes`).

Never create a scope folder without explicit confirmation.

### Step 4: Generate

Run the worker:

```
python scripts/bolt_cert.py --project [number] --scope "[scope]" --contractor "[contractor]" --project-name "[project name]"
```

Add `--signatory`/`--role`/`--phone` only to override the Nathan default. Add `--create-scope` only after the user confirmed in Step 3.

This copies both templates, fills placeholders via python-docx, saves the DOCX files, and renders a PDF for each via docx2pdf (Word COM, native quality). All four paths are printed.

### Step 5: Report

Report back to Nathan:
- The four file paths (2 DOCX + 2 PDF).
- The placeholder values used (contractor, project name, scope, signatory).
- Confirmation that the PDFs rendered.

---

## Worker Reference

`scripts/bolt_cert.py` flags:

| Flag | Purpose |
|---|---|
| `--project` | Project number (required). |
| `--scope` | QA scope folder name (required unless `--list-scopes`). |
| `--list-scopes` | List QA sub-folders for the project and exit. |
| `--contractor` | Head contractor for the Attn line (default: Northbridge Constructions). |
| `--project-name` | Project name for the regarding line (default: Riverside Eastern). |
| `--signatory` / `--role` / `--phone` | Signatory block (defaults: Nathan Hancock / Project Manager / 04XX XXX XXX). |
| `--create-scope` | Create the scope folder if missing. Only pass after user confirmation. |
| `--dest-subfolder NAME` | Write into `[scope]\NAME` instead of `[scope]`. Used for isolated testing so a live cert folder is never touched. |
| `--no-pdf` | Skip PDF rendering (DOCX only). |

---

## Critical Rules

- **Two documents, always.** PM16 (Methods) and PM17 (Certificate). Four files per run.
- **Never overwrite a live cert.** If a same-named file already exists in the scope folder, ask Nathan before re-running. For test runs use `--dest-subfolder`.
- **Never create a scope folder without confirmation.**
- **No long dashes** anywhere in output (hard workspace rule). Use a hyphen, colon, or restructure.
- **PDF requires Word** (docx2pdf uses Word COM on Windows). If Word is unavailable, run `--no-pdf` and tell Nathan the PDFs need a manual Save As, or convert later.
