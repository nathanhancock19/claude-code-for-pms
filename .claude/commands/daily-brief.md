# Daily Brief

Generate today's operating brief: priorities, risks, and next actions.

## Variables
(optional) focus: $ARGUMENTS

---

## Instructions

### Inputs to read
- `context/current-data.md`
- `context/strategy.md`
- `clients/*/status/` (most recent, if exists)
- Most recent files in `plans/` (optional scan)
- Most recent `data/reports/` (yesterday's brief)

### Output
Create a new report:

- `data/reports/daily-brief-YYYY-MM-DD.md`

Include:

1. **Today’s top 3 outcomes** (delivery first)
2. **Delivery status** (clients, blockers, risks)
3. **Sales/pipeline actions**
4. **Content actions**
5. **Quick wins**
6. **What to delegate**
7. **If I only have 2 hours today, do this**

Keep it action-oriented.
