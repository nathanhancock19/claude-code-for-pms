# /update-status

> Append a dated status update to the relevant plan file. Quick checkpoint so the next session (or terminal) picks up cleanly without re-reading the whole conversation.

## Variables

$ARGUMENTS: optional. Can be:
- A path to a plan file (e.g. `plans/2026-05-26-gc-industrial-voice-capture-implementation.md`)
- A short project slug (e.g. `gc-capture`, `tender-takeoff`, `scott-blake`)
- Empty — Claude figures out which plan or context file is the live one from the current session's work

Examples:
- `/update-status`
- `/update-status gc-capture`
- `/update-status plans/2026-05-26-gc-industrial-voice-capture-implementation.md`

---

## Instructions

### Step 1: Identify the target

If `$ARGUMENTS` is a path, use it directly. Otherwise:

1. If a slug is given, search `plans/` for the most recent matching plan file.
2. If empty, look at what was actually built/edited this session via `git status` and `git diff --stat HEAD`. The plan file with the most-edited related code wins.

If no plan file is found, ask once: "No plan file matched. Should I create `plans/YYYY-MM-DD-<slug>.md` or update `context/current-data.md` instead?"

### Step 2: Gather the facts (from this session, not from memory)

Don't fabricate. Use only what's verifiable:

- `git status` and `git diff --stat HEAD` for files changed
- `git log --since='4 hours ago' --oneline` for any commits made
- Recent tool calls in this session: tests run, scripts executed, smoke results, errors hit
- Any user-stated outcomes from this session ("works", "deployed", "broken", "blocked")

Don't quote files you didn't touch. Don't claim something passed if you didn't see it pass.

### Step 3: Update the target plan

Append a new dated block to the plan's `## Implementation Notes` section. If the section doesn't exist, create it. Use this shape:

```markdown
### Session checkpoint — YYYY-MM-DD

**State:** <one short sentence on where this build sits as of now>

**Done this session:**
- <concrete thing built or fixed, with file path if relevant>
- <next concrete thing>

**Verified:**
- <test or check that actually passed, with the evidence — log line, message id, smoke output>

**Open / blocked:**
- <thing still pending, with who owns it (Nathan / external / Claude)>

**Resume here:**
- <one or two lines: literally what to do first when re-opening this in a new terminal>
```

Keep each bullet to one line where possible. The whole block should fit on one screen.

### Step 4: Index touches

If the build has user-visible artefacts that aren't in `index.md` yet, add one line under the right section.

If the build touched `context/current-data.md` priorities, update the matching entry. Otherwise leave it alone.

### Step 5: Optional HISTORY.md line

If this is a substantive milestone (not just a tiny fix), append one line to `HISTORY.md` under today's date heading. One line, no preamble.

If today's date heading doesn't exist in `HISTORY.md`, create it.

### Step 6: Report

Print:
- Path to the file you updated
- The new "Resume here" line, verbatim, so the user can confirm it matches their mental model
- Any follow-up files touched (index.md, HISTORY.md, current-data.md)

Keep the report under 10 lines.

---

## Rules

- **No fabrication.** If you didn't see a check pass, don't write "passed". Use "tested" only if you actually ran it.
- **No marketing language.** "Works", "broken", "blocked", "deployed" — flat verbs.
- **No status inflation.** "Implemented" means the code is in and exercised. "Built" means the code is in but not exercised. "Drafted" means written but not run.
- **Append, don't replace.** Older session checkpoints stay in the plan as history.
- **Australian English** spelling. No em dashes anywhere.
- **One screen.** If your update is over ~20 lines, you're including too much. The point is a fast catch-up signal, not a session log.

---

## When NOT to use this command

- Mid-task. Wait until a logical checkpoint (test passed, deploy done, blocker hit).
- For purely conversational sessions with no code or file changes. There's nothing to checkpoint.
- For trivial doc edits or one-line fixes that don't change the plan's state.
