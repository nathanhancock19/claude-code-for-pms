# /dunsteel-variation-pack — Variation Supporting Document Pack (Project 501)

Compile supporting documentation for one or more Project 501 Riverside variations in response to a Northbridge Constructions request.

## Variables

args: $ARGUMENTS — variation numbers and optional context (e.g. "1 2 3 5" or "1 2 3 5 contracts admin requesting for payment claim")

---

## Instructions

### Step 1 - Parse Request

Extract variation numbers from $ARGUMENTS.

Project variations folder: `S:\Operations\01 Current Project\501 - Northbridge Constructions - Stratus SYD2\04 Variations`

Known Northbridge contacts on Project 501:
- Sam Taylor (contact1@headcontractor.example.com) - Trade Coordinator - PRIMARY
- Contact Two (contact2@headcontractor.example.com)
- Contact Three (contact3@headcontractor.example.com)
- Contact Four (contact4@headcontractor.example.com)

---

### Step 2 - Scan Each Variation Folder

For each variation number N, using Bash:

```
ls "S:\Operations\01 Current Project\501 - Northbridge Constructions - Stratus SYD2\04 Variations\V0N*" 2>&1
ls "S:\Operations\01 Current Project\501 - Northbridge Constructions - Stratus SYD2\04 Variations\VN*" 2>&1
```

Then use the Read tool on the main variation PDF (filename contains `V[N]` and ends in `.pdf` but does NOT contain "Correspondence", "Markup", or "Correspondence"):

Extract from the PDF:
- Scope of work (the description paragraph)
- Key line items (site labour, materials, transport)
- Total value (subtotal + margin + TOTAL)
- Date submitted
- Revision number

---

### Step 3 - Categorise Supporting Documents

For each variation folder, classify every file found:

| Category | Patterns | Notes |
|---|---|---|
| Cost estimate PDF | `V[N] [title].pdf` | Main deliverable |
| Takeoff / Breakdown | `*.xlsx` | Underlying data |
| Email correspondence | `*Email Correspondence*.pdf`, `*Correspondence*.pdf` | Proof of ITP |
| Markup / drawings | `*Markup*.pdf`, `*Southern*.pdf`, `*.png`, `*.jpg` | Technical backup |

Flag as **MISSING** if no email correspondence PDF is present.

**Important note:** The V03 folder email correspondence also covers V5. Both variations share the "Eastern Gantry Temp. Handrails Variation" thread. If V5 is requested and V5 has no correspondence PDF, check V03 folder first before flagging as missing.

---

### Step 4 - Handle Missing Email Correspondence

For each variation with no correspondence PDF:

**Option A - Try Outlook search script:**
```
python scripts/search_outlook_variations.py --variations "V[N] [scope keywords]"
```

If this returns results, report the email thread details.

**Option B - If script unavailable or auth fails, generate targeted Outlook search guidance:**

Derive 2-3 keywords from the variation scope. Provide:
- Outlook search string: `from:headcontractor.example.com [keywords]`
- Date range to search (based on variation submission date, go back 2-4 weeks)
- Instruction: find the thread > right-click > Print > Microsoft Print to PDF > save to variation folder as `V[N] Email Correspondence.pdf`

This follows the pattern already used in the V03 folder.

---

### Step 5 - Compile Pack Summary

Create: `outputs/501-riverside/variations/501-variation-pack-[YYYY-MM-DD]-V[numbers].md`

Include:
1. Pack status table (one row per variation: title, value, date, each doc type Found/MISSING, overall status)
2. Per-variation detail block: scope, value, date, file list, email evidence summary
3. Attach checklist: exact filenames to attach when sending to Northbridge
4. Action required section: Outlook search instructions for any missing correspondence
5. Cover email draft (inline, ready to copy)

Date format throughout: DD/MM/YYYY (Australian).
No em dashes anywhere.

---

### Step 6 - Draft Cover Email

Draft a cover email from Nathan to the requesting Northbridge contact (use Sam Taylor unless a different contact is specified in $ARGUMENTS).

Voice: direct, plain, no corporate vocab. No formal sign-off - just "Thanks, Nathan" or similar.

Include:
- What triggered the email (responding to their request)
- What is attached per variation
- Any items still being compiled and when they will follow
- One-liner if everything is included

Save to: `outputs/501-riverside/emails/[YYYY-MM-DD]-variation-pack-response.md`

Also save the full pack summary before the email draft in the variations output file.
