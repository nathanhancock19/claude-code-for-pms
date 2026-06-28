# Update Docs

Keep the OS healthy: update conditional docs, client indexes, and templates.

## Variables
(optional) $ARGUMENTS

---

## Instructions

### 1) Update `index.md`
- Ensure links match real file paths
- Keep it short (map only)

### 2) Update client READMEs
For each `clients/<client>/`:
- ensure `README.md` exists
- include links to: context, latest spec, latest issues, delivery docs

### 3) Promote reusable assets
If you see repeated patterns in a client build:
- extract into `templates/` (spec/runbook/prompt patterns)
- add a note to `docs/30-engineering-os.md` (if it changes process)

### 4) Finish
Output:
- which docs were updated
- what changed
