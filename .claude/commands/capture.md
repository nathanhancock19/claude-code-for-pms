# /capture — Quick Content Idea Capture

> Capture a content idea and classify it as a stub in the pipeline.

## Variables

$ARGUMENTS (the raw idea, topic, or observation to capture)

## Instructions

You are capturing a content idea into the pipeline. Quick classification, duplicate check, store as stub. For full concept development, use `/develop`.

### Step 1: Understand the Idea

Extract the core in 1-2 sentences from the user's input.

### Step 2: Check for Duplicates

```bash
source .venv/bin/activate && python3 -c "
import sys, sqlite3; sys.path.insert(0, '.')
from scripts.db import get_connection
conn = get_connection()
rows = conn.execute(\"\"\"
    SELECT id, title, production_status FROM content_ideas
    WHERE title LIKE '%KEYWORD%' ORDER BY created_at DESC LIMIT 5
\"\"\").fetchall()
for r in rows: print(f'  #{r[\"id\"]} [{r[\"production_status\"]}] {r[\"title\"]}')
if not rows: print('  No duplicates found.')
conn.close()
"
```

Replace KEYWORD with the most distinctive word from the idea.

If duplicates exist, tell the user and ask if they want to proceed or develop the existing one instead.

### Step 3: Classify

Read `templates/content-pipeline/strategy.md` to understand their platform, pillars, and audience segments.

Determine:
- **Channel:** Their primary platform (from strategy.md)
- **Format:** Appropriate format for that platform (from strategy.md format types)
- **Content pillar:** Which of their defined pillars this falls under
- **Funnel position:** awareness / consideration / conversion

Present the classification to the user for confirmation.

### Step 4: Store as Stub

After confirmation:

```bash
source .venv/bin/activate && python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.db import get_connection
from scripts.writer import write_content_idea

idea = {
    'title': 'TITLE_HERE',
    'description': 'DESCRIPTION_HERE',
    'channel': 'CHANNEL',
    'format_type': 'FORMAT',
    'source_type': 'manual',
    'content_pillar': 'PILLAR',
    'funnel_position': 'POSITION',
    'notes': 'NOTES',
}

conn = get_connection()
idea_id = write_content_idea(conn, idea)
conn.close()
print(f'Stored as stub #{idea_id}')
"
```

### Step 5: Regenerate Pipeline

```bash
source .venv/bin/activate && python3 scripts/generate_pipeline.py
```

### Step 6: Report

Tell the user:
- Stub #{id} captured
- Channel + format + pillar
- "Run `/develop #{id}` to flesh it out with strategic positioning and packaging."

$ARGUMENTS
