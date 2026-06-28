> ⚠️ Requires git to be initialized first: run `git init` in workspace root before using this command.

# Commit

> Save your work, update documentation, and keep the changelog current — all in one command.

## Variables

message: $ARGUMENTS (optional — override commit message, or leave blank for auto-generated)

---

## Instructions

You are committing changes to the workspace. This command does three things:
1. Creates a clean Git commit with a structured message
2. Checks if any technical documentation needs creating or updating
3. Updates HISTORY.md with what was done

Follow this workflow exactly.

### Step 1: Understand What Changed

Run `git diff HEAD` to see all changes (staged and unstaged). Also run `git status` to see untracked files.

Review the changes and understand what was done. Summarize it mentally — you'll need this for the commit message, doc check, and changelog.

### Step 2: Stage Changes

Stage the relevant changed files. Prefer staging specific files by name rather than `git add -A`, to avoid accidentally committing sensitive files.

**Never stage:**
- `.env` or any credential files
- `data/*.db` or `data/*.db-*` (database files)
- `__pycache__/` or `.venv/`
- Any file that should be gitignored

If in doubt, ask the user: "I'm about to stage these files: {list}. Look good?"

### Step 3: Generate Commit Message

If `$ARGUMENTS` was provided, use that as the commit message.

If no message was provided, generate one using this format:

```
<type>: <description>
```

**Types:**
- `feat` — New feature, command, or capability
- `update` — Enhancement to existing feature or content update
- `fix` — Bug fix or correction
- `data` — Data collection, backfill, or schema changes
- `context` — Strategy, project, or business context updates
- `docs` — Documentation changes
- `refactor` — Code restructuring without behavior change
- `chore` — Maintenance, dependency updates, config changes

**Rules:**
- Present tense verbs (add, fix, update — NOT added, fixed, updated)
- 50 characters or less for the first line
- No period at the end
- Be specific about what changed

**Examples:**
- `feat: add data collection pipeline for Stripe`
- `update: restructure business strategy for Q2`
- `fix: correct revenue calculation in dashboard`
- `docs: update data pipeline system doc`

If the commit includes changes across multiple areas, use the most significant type and summarize. Add a body with bullet points for detail.

### Step 4: Commit

Run the commit:

```bash
git commit -m "$(cat <<'EOF'
<commit message here>
EOF
)"
```

Run `git status` after committing to verify success.

### Step 5: Documentation Check

**Skip this step entirely for `fix`, `chore`, `refactor`, and `docs` type commits.**

For `feat` and `update` commits that touch system files (`scripts/`, `apps/`, `.claude/commands/`, `.claude/skills/`):

1. **Read `index.md`** (workspace root) to see which systems already have docs listed under `docs/`
2. **Assess the changes:**
   - Did this create a NEW system that doesn't have a doc? → Create one
   - Did this significantly change an EXISTING documented system? → Update the doc
   - Minor changes within a system (small bug fixes, config tweaks)? → Skip
3. **If creating a new doc:**
   - Name it descriptively in `docs/`: use short kebab-case (e.g., `docs/infra-os.md`, `docs/site-diary-bot.md`)
   - Model it after existing docs — 60-120 lines, covering: purpose, architecture, key files, usage, known issues
   - Add a bullet to `index.md` under **System Documentation** with the path and a one-line description
   - Stage and commit the new doc separately: `docs: add system doc for {system name}`
4. **If updating an existing doc:**
   - Read the current doc
   - Update the sections that changed
   - Stage and commit: `docs: update {system name} doc`
5. **Tell the user** what you did: "Created doc for X" / "Updated doc for Y" / "No doc update needed — change was minor."

### Step 6: Update HISTORY.md

**For `feat` and `update` commits:**

If `HISTORY.md` doesn't exist yet, create it with a `# HISTORY` heading and a one-line description before adding the first entry.

Append an entry to `HISTORY.md` under today's date. If today's date section already exists, add to it. If not, create a new section at the top.

Format:
```markdown
## YYYY-MM-DD

### [Brief Title]
- What was done (bullet points)
- Key files touched
```

Keep it concise — 2-5 bullet points. Focus on WHAT was built and WHY, not HOW.

Stage and commit:
```
docs: update changelog
```

**For `fix`, `context`, `data` commits:** Only update HISTORY.md if the change was significant. Minor fixes don't need a changelog entry.

**For `chore`, `refactor` commits:** Skip HISTORY.md.

### Step 7: Suggest Push

After everything is committed, suggest pushing:

"All committed. Want me to push to GitHub? (This backs up your work to the cloud.)"

If they say yes:
```bash
git push
```

If they say no, that's fine — they can push later.

### Step 8: Confirm

Report:
- The commit message(s) used
- Files committed
- Whether docs were updated (and which ones)
- Whether HISTORY.md was updated
- Current branch and push status

---

## Critical Rules

- **Never commit secrets** — No `.env`, credentials, API keys. Warn if any are staged.
- **Never push without asking** — Always ask before pushing. The user might want to review first.
- **Never amend** — Create new commits, not amends, unless the user explicitly asks.
- **Never force** — No `--force`, `--no-verify`, or destructive git operations.
- **Review before committing** — Always show what will be committed and get confirmation if changes are large or touch sensitive areas.
- **Doc updates are separate commits** — Keep the main work commit clean. Documentation goes in a follow-up commit.
