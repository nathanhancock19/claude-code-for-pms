# /dunsteel-summarise — Contract and Document Summarisation

Summarise a contract, subcontract agreement, technical specification, or long document for Nathan Hancock, PM at Dunsteel.

## Variables

input: $ARGUMENTS (file path, or leave blank to paste)

---

## Instructions

### Step 1 — Load the Document

If `$ARGUMENTS` is a file path: read that file.
If `$ARGUMENTS` is empty: say "Paste the document text and I'll summarise it." Wait for Nathan to paste before proceeding.

Also read `context/pm-context.md` to identify which project this document relates to (match by builder name, project number, or site location if possible).

### Step 2 — Identify Document Type

Determine what type of document it is:
- Subcontract agreement
- Head contract clause or amendment
- Supplier quote or proposal
- Builder letter or formal notice
- Technical specification or drawing note
- Programme or schedule
- Other

### Step 3 — Summarise

Produce a structured summary using this format:

```
## Document Summary

**Type:** [contract / agreement / notice / quote / spec / programme / other]
**Parties:** [who is involved]
**Date:** [document date if present]
**Project:** [project number and name if identifiable]

---

## Key Obligations — Dunsteel
[What Dunsteel is required to do. Bullet list. Be specific — not "comply with all requirements" but the actual obligations.]

## Key Obligations — Other Party
[What the other party is required to do. Bullet list.]

## Critical Dates and Deadlines
[Any dates, timeframes, notice periods, milestones, programme constraints. If none present, say "None stated."]

## Financial Terms
[Contract value, payment terms, variations, retention, penalties, liquidated damages — if present. If none, say "Not applicable."]

## Risk Flags
[Anything unusual, one-sided, potentially problematic, or worth escalating. Flag clauses that are heavily weighted against Dunsteel.]

## Action Items for Nathan
[What Nathan needs to do now, soon, or before a specific deadline — ordered by urgency.]
```

Keep each section tight: 3-7 bullets maximum. Flag anything Nathan should read himself rather than relying only on this summary. If a clause is complex or unusual, quote it directly rather than paraphrasing.

### Step 4 — Output

Present the summary. Then ask:
> "Want me to go deeper on any section, or draft a response?"
