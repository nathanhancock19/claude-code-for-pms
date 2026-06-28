# Prime

> Execute the following sections to understand the workspace then summarize your understanding.

## Run

ls -la
find . -type f -name "\*.md" | head -20

Refresh the per-project status digest (best effort - it pulls the last 2 days of
events from the Supabase store and rewrites each active project's status.md). If
it errors (offline, no API key), note it and carry on:

python scripts/project_digest.py --days 2

## Read

CLAUDE.md
./context
reference/projects/*/status.md   (the freshly refreshed per-project "what's happening now")

## Summary

After reading, provide:

1. A brief summary of who I am, what this workspace is for and what your role is
2. Your understanding of the workspace structure and the purpose of each section/file
3. What commands are available
4. A summary of my/our current strategies and priorities
5. **What's hot right now per project** - a short roll-up from the status.md files: which projects have action items, RFIs awaiting reply, or deadlines this week
6. Confirmation you're ready to help me with pursuing these goals through use of this workspace
