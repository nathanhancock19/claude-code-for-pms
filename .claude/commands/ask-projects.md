# Ask Projects

> Answer a plain-language question about the live project event stream (the
> Supabase `project_events` store fed by the email router). You are the
> intelligence: pull the right slice with the query helper, then reason over it
> and answer. Do not call any external model - you are already Claude.

## Input

`$ARGUMENTS` is the question, e.g.:
- "what's hot on 501 this week"
- "what variations are unresolved"
- "what's due this week"
- "where am I waiting on a head contractor"
- "what happened on 505 in the last fortnight"
- "any RFIs open across all projects"

## How to answer

1. **Pick the query.** Map the question to `scripts/project_events_query.py` flags
   (filters AND together, default window is 14 days):

   | Question shape | Command |
   |---|---|
   | A specific project | `python scripts/project_events_query.py --project 501 --days 14` |
   | A type across all projects | `python scripts/project_events_query.py --type Variation --days 30` |
   | RFIs / decisions open | `--type RFI --days 30` (also run `--type Decision`) |
   | Due / deadlines soon | `--due-before YYYY-MM-DD --days 60` |
   | Recent activity, all projects | `--days 7` |
   | Everything on a project, long view | `--project 504 --days 60` |

   Types are: Delivery, Variation, RFI, Decision, Schedule Change, Finish
   Approval, Site Issue, Commercial, General. Run more than one query if the
   question spans types (e.g. "open queries" = RFI + Decision).

2. **Read the output and answer concisely.** Lead with the direct answer, then
   the specifics: project, date, who, the deadline, and what it needs. Group by
   project when the question is cross-project. Quote the event subject so Nathan
   can find it. Plain Australian English. No long dashes.

3. **Be honest about the data:**
   - **Reply-state is not stored.** The store does not know if Nathan has
     replied. For "where am I waiting" questions, answer from RFI / Decision /
     Schedule Change events and their due dates, and say it is based on the event
     type, not confirmed reply-state. (The chase/stale engine, Direction 3, will
     add live reply-state.)
   - **Due dates are AI-suggested** by the classifier, not authoritative. Flag
     anything time-critical for Nathan to confirm.
   - Recurring platform notifications are collapsed (the output marks them
     `(recurring: N collapsed)`); treat them as one item.

4. **If nothing matches,** say so plainly and suggest widening the window
   (`--days 30`) or check the project id.

## Note

This reads the same store the email router writes every 15 min on the VPS, so it
is always current. It needs `SUPABASE_URL` / `SUPABASE_API_KEY` in `.env`
(present locally and on the VPS).
