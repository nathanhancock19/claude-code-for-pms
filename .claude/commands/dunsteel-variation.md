# /dunsteel-variation - Create a New Variation (PM08 Excel)

Create a new, auto-numbered variation folder and cost-estimate Excel from the
PM08 template, in the project's S: drive "04 Variations" folder, with the header
pre-filled.

> This is NOT the same as `/dunsteel-variation-pack`. The pack compiles existing
> supporting documents for variations already submitted to Northbridge Constructions. This
> command creates a brand new variation from the PM08 template.

## Variables

args: $ARGUMENTS - project number followed by the variation title
(e.g. `501 HV Room Stair Access Modifications`)

---

## Instructions

### Step 1 - Parse the request

From `$ARGUMENTS`:
- First token = **project number** (e.g. `501`, `505`, `504`).
- Everything after = **variation title** (e.g. `HV Room Stair Access Modifications`).

If no project number or no title is given, ask Nathan for the missing piece.
Do not guess a title.

### Step 2 - Run the worker script

The Python worker does all the file work: project lookup, auto-numbering, folder
creation, template copy, header pre-fill, and opening the file. Run it from the
workspace root:

```
python scripts/dunsteel_variation.py <number> <title...>
```

Example:

```
python scripts/dunsteel_variation.py 501 HV Room Stair Access Modifications
```

The title does not need quoting (all trailing tokens are joined), but quoting is
also fine.

What the script does, in order:
1. Finds the project folder under `S:\Operations\01 Current Project\` matching
   `<number> -*` (e.g. `501 - Northbridge Constructions - Stratus SYD2`). Errors cleanly if
   there is no match or more than one match.
2. Opens `<project>\04 Variations\`.
3. Scans existing `V[NN] ...` folders, takes the highest number, adds 1.
   `VX ...` (held/rejected) folders are ignored for numbering.
4. Creates `V[NN] <title>\`.
5. Copies `S:\Operations\11 Project Management Database\Project Management
   Templates\PM08. Variation Template.xlsx` into it (raw byte copy, so the rates
   Index sheet, VLOOKUPs, data validation and tracker tables are preserved
   exactly).
6. Renames the copy to `V[NN] <title>.xlsx`.
7. Pre-fills only the named header cells (see mapping below). It never edits any
   formula, VLOOKUP, SUM, or tracker-table cell.
8. Opens the file via the Windows `start` command.

### Step 3 - Report back

Read the script's output. Report to Nathan:
- The variation label and title (e.g. `V10 HV Room Stair Access Modifications`).
- The full folder path and the full file path.
- Any warnings the script printed (for example: a title that had to be
  sanitised, or a project with no stored header facts so CLIENT / Job Name /
  Job Address were left blank for manual entry).
- Confirm the file was opened.

Keep it short. Nathan just needs to know it is done and where it is.

---

## Header cell mapping (for reference)

Verified against the live PM08 template AND the existing 501 V04 / V08 / V09
files. The real files are the source of truth. The worker writes only these
cells:

| Field           | Cell  | Notes                                          |
|-----------------|-------|------------------------------------------------|
| CLIENT          | A3    | head contractor; cell D65 mirrors it via `=A3` |
| Job Name / code | A5    | e.g. `STSYD02` (template default is `RLC`)     |
| Job Address     | D5    | e.g. `[site address]`                          |
| Job No.         | B11   | integer, e.g. `501`                            |
| Variation #     | B12   | integer, e.g. `10`                             |
| Variation Title | C13   | the title text                                 |
| Date In         | B8    | already `=TODAY()`, left untouched             |
| Revision        | B9    | already `A`, left untouched                    |

Per-project header facts (CLIENT, Job Name, Job Address) live in the
`PROJECT_HEADERS` dictionary in `scripts/dunsteel_variation.py`. Project 501 is
populated. When a new project's first variation is created and it is not yet in
that dictionary, the script still creates the file but leaves those three fields
blank and warns. To add a project, append its facts to `PROJECT_HEADERS`.

---

## Behaviour notes

- **Idempotent.** Rerunning with the same project and title reuses the existing
  folder and leaves the existing file untouched. It does not create a duplicate
  or burn a new number.
- **Project not found.** If the number matches no folder (or several), the script
  exits cleanly with an explanation rather than guessing.
- **Invalid filename characters.** Characters Windows forbids in names
  (`< > : " / \ | ?  *`) are stripped from the title and a warning is reported.
- **Auto-open.** The file opens automatically. Pass `--no-open` to suppress this.

---

## Critical rules

- Do NOT rebuild the PM08 template. Copy it and pre-fill the header only.
- Do NOT touch formulas, VLOOKUPs, SUMs, the Index rates sheet, or the tracker
  tables on the right of the Variation sheet.
- Do NOT overwrite or modify any existing real `V[NN]` folder or file.
- No em dashes anywhere, in this file, the script, or any generated content.
  Use a hyphen, a colon, or restructure.
