# /dunsteel-project-review — Post-Project Review Slide Deck

Build a complete Dunsteel post-project review as a PowerPoint slide deck (16:9, dark navy) using python-pptx. Output is a .pptx file ready to open in PowerPoint.

## Variables

args: $ARGUMENTS — project number and/or name (e.g. "501 Riverside" or "512")

---

## Instructions

### Step 1 - Parse Request

Extract the project number and name from $ARGUMENTS.
Check `context/current-data.md` and `outputs/` for any existing data on this project.

---

### Step 2 - Scan Image Folder

Check for images at `outputs/[project-slug]/review-images/`.
List all subfolders present.

| Subfolder | Used on slide |
|---|---|
| `cover/` | Cover slide - first image as hero (right panel) |
| `estimating/` | Estimating slide - image strip at bottom |
| `drafting/` | Drafting slide - image strip at bottom |
| `fabrication/` or `workshop/` | Fabrication slide - image strip at bottom |
| `painting/` or `coating/` | Painting slide - image strip at bottom |
| `site-install/` or `install/` | Site Install slide - image strip at bottom |
| `safety/` | Safety slide - image strip at bottom |
| `photos/` or `gallery/` | Project Photos slide - full grid |

If no image folder exists, generate without images and note which subfolders to create.

**Budget screenshot:** Check `reference/[project-folder]/budgets.PNG` first, then `review-images/budget/`. Use it full-slide on the budget slide if found. If not found, build the table from data provided.

---

### Step 3 - Collect Review Data

Present this questionnaire. Nathan can fill in the format or give a brain dump - extract from whichever.

```
PROJECT BASICS
- Project number:
- Project name / location:
- Client / Principal Contractor:
- Scope of works (1-2 sentences):
- Install crews / subcontractors (names):
- Project Manager:
- Review date:

FINANCIALS (from SimPro)
- Original contract value $:
- Approved variations total $:
- Final contract value $:
- Total cost $:
- Gross profit $ and %:
- % invoiced (and whether closed out):

BUDGET VS ACTUAL (from Power BI screenshot or manual data)
- Site Labour: budget $ / actual $ / variance $
- Crane Hire: budget $ / actual $ / variance $
- Drafting: budget $ / actual $ / variance $
- Materials: budget $ / actual $ / variance $
- Workshop: budget $ / actual $ / variance $
- Other cost codes if relevant:

TOP 3 PROJECT HIGHLIGHTS
1.
2.
3.

DEPARTMENT REVIEWS
For each dept: 2-4 dot points of positives, 2-4 negatives. Skip if nothing meaningful.

ESTIMATING - positives / negatives:
DRAFTING - positives / negatives:
FABRICATION / WORKSHOP - positives / negatives:
PAINTING & COATING - positives / negatives:
SITE INSTALL - positives / negatives:
SAFETY - incidents (number) / positives / anything to flag:
PROJECT MANAGEMENT - what you'd do differently (honest PM reflection):

VARIATIONS
List each: [Scope] | [Description] | [Status - Charged or Absorbed] | [Outcome - Approved or Not Recovered]

PM LESSONS LEARNED (3-4 specific things to take forward - what happened, what it cost, what to do next time):

RATING (out of 5):

FINAL THOUGHTS
- The Standout (one thing that made this job):
- The Fix (main thing to fix before the next similar scope):
- The Relationship (client relationship outcome):
```

---

### Step 4 - Generate Python Script and Run

Read `scripts/generate_379_review.py` as the reference implementation. It contains the full set of proven helper functions. **Copy them exactly - do not reinvent.**

Write a new project-specific script at `scripts/generate_[project-slug]_review.py`.

**The script must:**
1. Copy all helper functions and constants verbatim from the 503 reference (rect, oval, pic, tx, tx_lines, header, badge, card, card_h, stat_circle, dept_slide, photo_grid, and all design constants)
2. Update the three PATH constants (ROOT stays the same, update IMGS, BUDGET, OUT for the new project)
3. Replace each slide builder function with the new project's content
4. Include the standard build() function and `if __name__ == "__main__":` block

**Slide sequence (same every time):**
1. Cover
2. Project Overview (3 highlights + key people)
3. Budget Performance (always included - screenshot or manual table)
4. Project Information (financial summary table + stat circles)
5. Department Reviews intro (2x3 dept grid)
6-11. Per department - one slide each, skip if no content provided
12. Variations and Commercial
13. PM Lessons Learned
14. Project Summary (stat circles + action cards)
15. Final Thoughts
16. Project Photos (only if photos/ subfolder has images)
17+. Any gallery slides for depts with many images (e.g. a painting gallery if there are painting photos worth showing large)

---

### Language Rules

Write in Nathan's voice - direct, plain, like he's talking to someone who was on the job.

Good: "Workshop came in $5k under - no drama there."
Bad: "The workshop expenditure was within budgeted parameters."

Good: "$190k over on a single code is too big to miss twice."
Bad: "It would be advisable to improve the accuracy of future labour estimations."

- Keep card body text to 2-3 lines maximum
- Be specific with numbers - "$190k over" not "significantly over"
- No em dashes anywhere - use a hyphen or colon instead
- Never invent figures, names, or facts not provided

---

### Design Rules

Use the exact constants and helpers from the 503 reference. Key values:

```python
# Colours
BG   = RGBColor(0x11, 0x18, 0x27)   # slide background
CARD = RGBColor(0x1e, 0x29, 0x3b)   # card background
DARK = RGBColor(0x0f, 0x17, 0x2a)   # cover left panel
BLUE = RGBColor(0x3b, 0x82, 0xf6)   # accent
BLT  = RGBColor(0x60, 0xa5, 0xfa)   # headings
WHITE= RGBColor(0xf9, 0xfa, 0xfb)   # primary text
MUTED= RGBColor(0x9c, 0xa3, 0xaf)   # secondary text
GREEN= RGBColor(0x22, 0xc5, 0x5e)   # positives
AMBER= RGBColor(0xf5, 0x9e, 0x0b)   # negatives / overruns

# Layout (inches)
SW, SH = 13.33, 7.5    # slide dimensions
ML, MR = 0.55, 0.55    # margins
UW     = SW - ML - MR  # usable width = 12.23
CT     = 1.42           # content top
CH     = 5.68           # content height
LW     = UW * 0.55 - 0.12   # left col width
RW     = UW * 0.45 - 0.12   # right col width
RL     = ML + UW * 0.55 + 0.12  # right col left edge
```

Every slide gets the DUNSTEEL watermark via `header()`. Cover slide has no footer.

**Final thoughts slide - important:**
Do NOT use full-height fixed cards. Use `card_h(body)` to size each card to its content, same as dept slides. Place cards in a row across the slide starting at CT. Leave blank space below if the text is short - that is fine. The cards should not stretch to fill the full slide height.

**Budget slide:**
- If the screenshot exists: `pic(slide, BUDGET, ML, CT, UW, CH)` - fills the content area
- If no screenshot: build a table manually using rect() blocks, green for underspends, amber for overruns

---

### Step 5 - Run the Script

```
python scripts/generate_[project-slug]_review.py
```

PPTX saves to `outputs/[project-slug]/[project-number]-post-project-review-[YYYY-MM-DD].pptx`.

---

### Step 6 - Report Output

Tell Nathan:
1. PPTX path
2. Which image subfolders were found and used
3. Which subfolders are missing (so he knows where to drop photos)
4. One-line summary: scope, GP%, job rating

Note:
- To add images: drop files into the relevant subfolder and re-run the script (no content changes needed)
- To update content: edit the data in the script and re-run

No em dashes in any output.
