# /dunsteel-brief — Morning PM Brief

Generate a morning briefing for Nathan Hancock's PM day at Dunsteel.

## Variables

(optional) data: $ARGUMENTS — paste delivery data, or leave blank

---

## Instructions

### Step 1 — Load Context

Read:
- `context/pm-context.md` — active projects, key contacts, material lead times
- `context/current-data.md` — automation build status (for any tech-related flags)

### Step 2 — Fetch Live Project Forecast

Attempt to fetch the live Notion project forecast:
- **Notion page ID:** `YOUR_PROJECT_FORECAST_PAGE_ID`
- **Page title:** Project Forecast
- Use the Notion MCP tool `notion-fetch` with this page ID if available.

If the Notion MCP is not available or returns an error:
- Check if `$ARGUMENTS` contains pasted delivery data (Google Sheet export, table, or plain text list)
- If yes, parse it and use it as the delivery data source
- If no data is available, work from `context/pm-context.md` delivery notes only and flag: "No live delivery data — paste your Notion forecast or delivery sheet for a more accurate brief."

### Step 3 — Parse Delivery Data

From whichever source is available (Notion, pasted data, or pm-context.md), extract:
- Deliveries arriving today
- Deliveries arriving this week (next 5 working days)
- Deliveries with no confirmed ETA
- Deliveries marked as unconfirmed or pending booking
- Any items flagged as overdue or behind programme

### Step 4 — Check Material Ordering Windows

Using the Material Ordering Lead Times from `context/pm-context.md`:
- For each upcoming delivery, calculate whether the ordering window has passed or is approaching
- Flag any material that needs to be ordered this week to arrive on time
- Include: material type, project, why it's flagged, who to contact

If lead times are not yet filled in (`[X weeks]` placeholders), skip this section and note: "Fill in Material Ordering Lead Times in `context/pm-context.md` to enable ordering alerts."

### Step 5 — Generate the Brief

Output in this format — bullets only, no long paragraphs:

```
# Dunsteel PM Brief — [today's date, Australian format DD/MM/YYYY]

## Projects — Current Status
[One line per active project: stage, anything urgent or overdue]

## Deliveries Today
[What's arriving today, to which project, confirmed or not. If nothing: "No deliveries scheduled today."]

## Deliveries This Week
[What's arriving in the next 5 days. Flag anything unconfirmed or with no ETA.]

## Order Now (Action Required)
[Materials that need ordering this week based on lead times. Format: Material — Project — Order by [date] — Call/email [supplier]. If nothing: "Nothing urgent to order this week."]

## Flags and Issues
[Outstanding NCRs, overdue actions, disputes, contacts who haven't replied — pulled from pm-context.md current issues]

## Comms to Send Today
[Emails or calls prompted by the above — e.g. "Chase [contact] on ETA for [delivery]", "Reply to [builder] re [issue]". Use /dunsteel-email to draft any of these.]

---
If you only have 30 minutes this morning: [single most important action]
```

Keep every section tight. If a section has nothing to report, say so in one line — do not omit it.
