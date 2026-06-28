# Find on S: Drive

> Search the Dunsteel S: drive for project artefacts (drawings, variations, bolt certs, schedules, SWMS, markups) by natural-language query, using the standard project folder conventions. Learns over time.

## Variables

project: optional project number (e.g. `501`) - usually the first argument
query: the rest of the arguments (what you are looking for, in plain words)

## Usage

```
/find-on-sdrive 501 latest delivery schedule
/find-on-sdrive 501 V08 supporting docs
/find-on-sdrive 504 bolt cert level 4
/find-on-sdrive 501 programme
/find-on-sdrive cooling tower handrail markup
```

The first argument is normally the project number. Everything after it describes what you want. The project number is optional: if you leave it off (e.g. `cooling tower handrail markup`) the skill searches across all current projects, but that is slower, so give the number when you know it.

---

## What This Does

This is a thin wrapper around `scripts/find_on_sdrive.py`. It searches the S: drive only. It does not touch email - Outlook searches are handled by a separate skill.

Pipeline (all handled by the worker script):

1. **Parse the query** - pulls out the project number, the document type, and any scope or level keywords (e.g. "level 4", "stair 1", "V08", "cooling tower").
2. **Map type to folders** - uses the known Dunsteel folder conventions so it searches the right place instead of crawling the whole drive:
   - "delivery schedule" / "delivery" -> `13 Schedules/`
   - "programme" / "schedule" / "fabrication schedule" -> `13 Schedules/`
   - "variation" / "V[NN]" -> `04 Variations/` (and the specific `V[NN] ...` folder)
   - "bolt cert" / "bolt tightening" -> `08 QA/[scope]/` filtered to bolt-tightening files
   - "ITP" / "ITR" / "verticality" / "weld" / "gal cert" -> `08 QA/[scope]/`
   - "for approval" / "drawing" -> `03 Workshop Drawings/For Approval/` or the drawings root
   - "SWMS" / "prestart" / "rescue plan" -> `15 WHS and Environmental/`
   - "markup" -> `12 Markups/` (and variation markups)
   - "scope" -> `05 Scopes/`; "costing"/"quote"/"CTC" -> `09 Costing-Quotes/`; "O&M"/"completion" -> `10 Completion Documents/`
3. **Glob and rank** - searches those folders, then ranks by filename relevance, scope match, and modification time. "latest" or "newest" in the query boosts the most recent file; for "latest variation" it prefers the highest-numbered `V[NN]` folder.
4. **Return the top matches** - prints 5 to 10 results with full paths, modified dates, and a ready-to-run `start` command to open the top hit.
5. **Log to the learning index** - every query and its matched paths are appended to `reference/sdrive-index.json` so the skill compiles over time (which folders surface which document types).

---

## Instructions

Run the worker script and report the result.

### Step 1: Parse arguments

- `project` = the first token of `$ARGUMENTS` if it is a project number; otherwise there is no project filter.
- `query` = the remaining tokens (the description of what to find).

If `$ARGUMENTS` is empty, ask Nathan what he is looking for before running.

### Step 2: Run the search

```bash
python scripts/find_on_sdrive.py <project> <query>
```

For example:

```bash
python scripts/find_on_sdrive.py 501 latest delivery schedule
python scripts/find_on_sdrive.py 504 bolt cert level 4
python scripts/find_on_sdrive.py cooling tower handrail markup
```

Useful flags:

```bash
python scripts/find_on_sdrive.py 501 programme --limit 8   # more or fewer results (1..25)
python scripts/find_on_sdrive.py 501 latest variation --json  # JSON for programmatic use
python scripts/find_on_sdrive.py 501 programme --no-log     # do not update the index
```

### Step 3: Report

Report back to Nathan:
- The top matches with full paths and modified dates (the worker already prints these).
- The single best match, called out clearly.
- The `start "" "<path>"` command to open it.

If nothing matched, relay the worker's broaden-the-search suggestions and offer to run a wider query (drop the type filter, drop the scope filter, or use fewer keywords).

---

## Folder Conventions (reference)

Project root: `S:\Operations\01 Current Project\[number] - [head contractor] - [project name]\`

Every project carries the same 16 numbered sub-folders. The ones this skill targets:

| Folder | Holds | Triggered by |
|---|---|---|
| `03 Workshop Drawings` | For Approval, For Fabrication, RFI sub-folders, marking plans | drawing, for approval, RFI, marking plan |
| `04 Variations` | one `V[NN] [title]` folder per variation, plus PM08 template | variation, V08, latest variation |
| `05 Scopes` | scope task documents | scope |
| `06 Delivery Dockets` | delivery dockets | docket |
| `08 QA` | scope sub-folders (Level 2..7, Stair 1, HV Room, Defects, etc.) holding bolt certs, ITPs, ITRs, verticality, weld, gal certs | bolt cert, ITP, ITR, verticality, weld, gal cert |
| `09 Costing-Quotes` | commercial baseline, CTC | costing, quote, CTC, budget |
| `10 Completion Documents` | O&M manuals, handover | O&M, completion, handover |
| `12 Markups` | markups | markup |
| `13 Schedules` | programme files (.mpp/.pdf), delivery schedules, fabrication schedules, head contractor Programmes | delivery schedule, programme, schedule, forecast |
| `15 WHS and Environmental` | SWMS, prestarts, rescue plans, safety audits | SWMS, prestart, rescue plan, safety audit, WHS |

---

## Learning Index

`reference/sdrive-index.json` is seeded with an empty schema and grows on every query:

- `queries[]` - one record per query (timestamp, parsed project/type, match count, top matched paths)
- `path_hits` - rolling tally of which project sub-folder (e.g. `04 Variations`, `08 QA/Level 2`) produced matches
- `type_hits` - rolling tally by detected document type

Inspect or reset it:

```bash
python scripts/sdrive_index.py --show
python scripts/sdrive_index.py --reset
```

---

## Critical Rules

- **S: drive is read-only.** This skill never creates, moves, or deletes anything on the S: drive. The only file it writes is `reference/sdrive-index.json` in the workspace.
- **Outlook is out of scope.** Email searches are handled by a separate skill. If Nathan asks for emails, point him there instead of searching the S: drive.
- **No long dashes** anywhere in the skill, the script, or the output. Use a hyphen, a colon, or restructure.
- **Graceful on no match.** When nothing is found, the worker suggests a broader search rather than failing silently. Relay those suggestions.
