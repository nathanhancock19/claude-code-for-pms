# New Project

> Prime Nathan on a project whose S: drive folders already exist. Full pipeline: scrape the S: drive, scrape Outlook for related correspondence, create a Notion project page, and build the workspace reference folder (brief + S: drive index + Outlook summary + contacts). Wrapper around `scripts/new_project_prime.py`.

## Variables

project: first argument (project number, e.g. `432`)
flags: `--dry-run`, `--name`, `--months`, `--no-outlook` (passed through to the worker)

## Usage

```
/new-project 432
/new-project 432 --dry-run
/new-project 432 --name "Hartwell Group - King Street"
/new-project 432 --months 12
```

Single required argument: the project number. The skill finds the matching folder on the S: drive and parses `[number] - [head contractor] - [project name]` from the folder name.

---

## What This Does

Primes a project that admin has already set up on the S: drive. It runs six steps:

1. **S: drive lookup** - find the folder matching `[number] -*` under `S:\Operations\01 Current Project\`, falling back to `S:\Operations\02 Completed Projects\`. If more than one folder matches, the worker stops and reports the candidates so Nathan can disambiguate with `--name`.
2. **S: drive inventory** - a shallow, read-only walk of the project folder. Captures `01 Tender Information`, `02 Contractural` (Contract + Claims subfolders), `09 Costing-Quotes`, `04 Variations`, `05 Scopes`, `07 Installation Methodologys`, and the schedule folder (the name varies between projects, so it tries `13 Schedules`, `13 Program`, then `13 Programme`). Nothing on the S: drive is written, moved, or deleted.
3. **Outlook scrape** - searches Nathan's mailbox via Microsoft Graph for emails matching the project number, project name, and head contractor over the last 6 months (configurable with `--months`). Graph auth is detected first; see the Outlook section below.
4. **Notion page** - creates a page in the Dunsteel Projects Hub > Projects Database. In a normal run the skill layer creates this via the Notion MCP using the plan the worker emits. In `--dry-run` no page is created and the plan is printed instead.
5. **Workspace files** - creates or enriches `reference/projects/[number]-[slug]/` with `project-brief.md`, `s-drive-index.md`, `outlook-thread-summary.md`, and `contacts.md`.
6. **Context update** - adds or updates the project's row in `context/dunsteel-projects.md`. Skipped (reported only) in `--dry-run`.

---

## Instructions

Run the worker, then complete the two live external steps (Outlook + Notion) that the worker leaves to the skill layer.

### Step 1: Run the worker

For the first run on any project, or any unsupervised run, use `--dry-run`:

```bash
python scripts/new_project_prime.py <number> --dry-run
```

For a live run (creates the Notion page and updates the context table):

```bash
python scripts/new_project_prime.py <number>
```

The worker always writes the four local reference files (they are safe and idempotent) and writes a machine-readable plan to `reference/projects/[number]-[slug]/_new_project_plan.json`.

### Step 2: Outlook (only if the worker reported the scrape ran)

The worker performs the Outlook scrape itself when Graph auth is available. If it reports `Outlook scrape pending Graph credential`, do not attempt to work around it: the working Graph OAuth lives inside n8n (WF1) and is not reusable from a standalone script. Leave the placeholder `outlook-thread-summary.md` as written and report the gap. To enable it later, add `TENANT_ID` to `.env` and grant the app Mail.Read application permission (see the Outlook section).

### Step 3: Notion (live runs only)

In a live run, read `_new_project_plan.json` -> `notion_plan` and create the page via the Notion MCP:

- Resolve the Projects database with `notion-search` for "Projects" inside the Dunsteel Projects Hub.
- Create the page with `notion-create-pages`: title `[number] - [project name]`, and the properties from the plan (Project Name, Project Number, Status, Head Contractor, PM).
- Follow the existing 501 project-page pattern for sub-pages (Site Diary, Defects, Voice Memo Log, Variations). Prefer duplicating a template project page over building sub-pages from scratch.

In `--dry-run`, do NOT create the page. Report the plan exactly as the worker printed it, and leave a TODO note that Nathan should do the first real Notion-creating run himself.

### Step 4: Report

Report back to Nathan:
- The S: drive folder found and how it parsed.
- Inventory headline (how many target sections present, schedule folder name).
- Paths created or updated (the four reference files + the context row).
- The Notion plan (dry-run) or the created page URL (live).
- Outlook status: scraped, or the gap with the reason.
- Any open items the brief flags.

---

## Outlook (Microsoft Graph) Auth

The scrape uses the Microsoft Graph client-credentials flow against `nathanh@dunsteel.com.au`. It needs three values in `.env`: `CLIENT_ID`, `SECRET`, and `TENANT_ID`, plus an Azure app with Microsoft Graph **Mail.Read** application permission and admin consent.

`CLIENT_ID` and `SECRET` are present in `.env`, but `TENANT_ID` is not, and the live Graph OAuth currently runs inside n8n rather than as a reusable standalone credential. So the worker detects this, skips the scrape cleanly (it never hangs waiting for auth), and writes a placeholder `outlook-thread-summary.md` noting the gap. This is a known limitation, not a failure. To close it:

1. Add `TENANT_ID=<your Azure AD tenant GUID>` to `.env`.
2. Confirm the app behind `CLIENT_ID` has Mail.Read application permission with admin consent.
3. Re-run without `--dry-run`.

---

## Idempotency

Re-running on a project that already has a `reference/projects/[number]-*` folder enriches it in place: it never creates a duplicate folder or duplicate brief. The worker preserves hand-curated YAML values (project name, head contractor, end client, contract type, status, contact emails) and only fills blanks or refreshes the auto-extracted S: drive listings. The context-table update matches the existing row by project number and replaces it rather than appending.

---

## Output Locations

| Output | Location |
|---|---|
| Project brief | `reference/projects/[number]-[slug]/project-brief.md` |
| S: drive index | `reference/projects/[number]-[slug]/s-drive-index.md` |
| Outlook summary | `reference/projects/[number]-[slug]/outlook-thread-summary.md` |
| Contacts | `reference/projects/[number]-[slug]/contacts.md` |
| Machine plan (for the skill layer) | `reference/projects/[number]-[slug]/_new_project_plan.json` |
| Context row | `context/dunsteel-projects.md` active projects table (live runs only) |
| Notion page | Dunsteel Projects Hub > Projects Database (live runs only) |

---

## Critical Rules

- **S: drive is read-only.** The worker only lists folders. It never writes, moves, or deletes anything on the S: drive.
- **No unsupervised live Notion mutation.** The first real Notion-creating run should be supervised by Nathan. Default to `--dry-run` for unattended runs.
- **No long dashes** anywhere in the skill, the script, or any generated file. Use a hyphen, a colon, or restructure.
- **Do not invent project facts.** The brief flags commercial, scope, and contract fields as "TBC" until confirmed from the source documents. The auto-extracted file listings are real (read from disk); everything else is a prompt for Nathan to confirm.
